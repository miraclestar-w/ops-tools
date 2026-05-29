# Postgres不只是数据库：用PostgreSQL构建持久化工作流引擎

## 一、为什么要折腾这事儿

做过运维的同学应该都有这种体会：技术栈每多一个组件，半夜被闹钟叫醒的概率就多一分。Kafka要调，RabbitMQ要看集群状态，ZooKeeper动不动挂掉……对于中小团队来说，维护一套消息中间件的成本往往被严重低估。

我之前在项目里就遇到过这种尴尬：业务方要求加一个异步审批流，我第一反应是上Redis+Celery。结果光是部署和调优就花了两天，上线后又发现任务偶尔丢失——Redis挂了没持久化好。后来痛定思痛，琢磨出一个方案：既然PostgreSQL已经跑了，为什么不用它来当工作流引擎？

你没看错，就是那个你每天都在用的Postgres。

## 二、核心思路：用一张表搞定任务队列

整个方案的核心很简单——建一张任务表，把工作流拆成一个个"任务步骤"，用状态机来驱动执行。

```sql
CREATE TABLE workflow_tasks (
    id          BIGSERIAL PRIMARY KEY,
    task_type   VARCHAR(64) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    payload     JSONB NOT NULL DEFAULT '{}',
    priority    SMALLINT NOT NULL DEFAULT 0,
    max_retries SMALLINT NOT NULL DEFAULT 3,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by   TEXT,
    locked_at   TIMESTAMPTZ
);

CREATE INDEX idx_tasks_pending ON workflow_tasks (status, scheduled_at, priority DESC)
    WHERE status IN ('pending', 'retry');
```

看到没有？没有消费组，没有偏移量，没有分区。就是一张表，加上几个索引。PostgreSQL原生的MVCC和事务隔离已经帮你解决了并发控制问题——多个worker同时抢任务也不会出事。

## 三、抢任务的关键：SELECT FOR UPDATE SKIP LOCKED

这个方案能跑起来的核心武器是PostgreSQL 9.5引入的`SKIP LOCKED`语法。它让多个worker可以安全地并发抢任务，而不会互相阻塞：

```sql
BEGIN;

SELECT id, task_type, payload
FROM workflow_tasks
WHERE status IN ('pending', 'retry')
  AND scheduled_at <= NOW()
  AND locked_by IS NULL
ORDER BY priority DESC, scheduled_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;

-- 拿到任务后，更新状态
UPDATE workflow_tasks
SET status = 'processing',
    locked_by = 'worker-01',
    locked_at = NOW()
WHERE id = <拿到的id>;

COMMIT;
```

`SKIP LOCKED`的妙处在于：如果worker-01正在抢某条记录，worker-02会自动跳过它去抢下一条，而不是傻等锁释放。这比之前用`NOWAIT`然后捕获异常的方案优雅多了，而且性能差距在高并发场景下能有数量级的提升。

## 四、失败重试与死信处理

真实的生产环境里，任务执行失败是常态。我设计了一个简单的重试策略：

```sql
-- 任务执行失败时
UPDATE workflow_tasks
SET status = CASE
        WHEN retry_count < max_retries THEN 'retry'
        ELSE 'failed'
    END,
    retry_count = retry_count + 1,
    scheduled_at = NOW() + (interval '1 minute' * power(2, retry_count)),
    locked_by = NULL,
    locked_at = NULL
WHERE id = <task_id>;
```

指数退避，简单直接。`failed`状态的任务会被定期捞出来人工处理或发告警——相当于消息队列里的死信队列，但你不需要额外部署任何东西。

## 五、worker实现：一个Python脚本就够了

别把这事想复杂了。一个worker的核心循环大概长这样：

```python
import psycopg2
import json
import time

def run_worker(worker_id, poll_interval=1):
    conn = psycopg2.connect("dbname=myapp")
    
    while True:
        task = fetch_and_lock(conn, worker_id)
        if task is None:
            time.sleep(poll_interval)
            continue
        
        try:
            handler = HANDLERS[task['task_type']]
            handler(task['payload'])
            mark_complete(conn, task['id'])
        except Exception as e:
            mark_failed(conn, task['id'], str(e))
        
        conn.commit()
```

对，就是个无限循环：抢任务、执行、更新状态。没有worker pool的概念，没有broker连接，连消息序列化都是PostgreSQL原生支持的JSONB。整个worker进程只依赖一个psycopg2连接，资源占用极低。

如果你用Go写，配合`pgx`驱动，一个worker goroutine的内存占用可以控制在几MB以内。我们线上跑了几十个worker实例，总资源消耗还比不上一个Redis集群的零头。

## 六、工作流编排：状态机模式

单个任务只是基础，真正有价值的是多步骤工作流。比如一个典型的部署流程：代码审核→构建镜像→推送仓库→部署到staging→集成测试→部署到production。

我的做法是在任务表里加一个`workflow_id`字段，把同一流程的步骤串起来：

```sql
CREATE TABLE workflow_definitions (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(64) UNIQUE NOT NULL,
    steps       JSONB NOT NULL
);
```

`steps`字段用JSONB存储有序步骤定义，每完成一步，worker根据定义自动创建下一步的任务。整个流程的进度可以通过`workflow_id`随时查询，出了问题也能精确回溯到哪一步卡住了。

## 七、踩过的坑

说几句实话。这个方案不是银弹，有几个坑你得知道：

**性能天花板**：单表并发写入在千万级数据量后会明显下降。我们的解法是定期归档已完成任务到历史表，把热表控制在百万行以内。

**没有延迟消息原生支持**：`scheduled_at`只能轮询，不如Kafka的延迟队列精确。对于精度要求高的场景（比如毫秒级定时任务），还是得用专门的工具。

**Worker心跳缺失**：如果worker挂了，`locked_by`会一直挂着。我写了个定时任务，每5分钟清理锁定超过10分钟的任务，把它改回`pending`状态。

## 八、什么时候该用这个方案

说到底，这套方案适合这些场景：

- 项目初期，团队小，运维人手紧张
- 异步任务量不大（日均几万到几十万级）
- 希望减少技术栈复杂度，降低故障面
- 已经重度依赖PostgreSQL，不想引入新的中间件

如果你已经有成熟的Kafka集群，任务量日均过千万，那该用还是用。但对于绝大多数中小项目来说，PostgreSQL已经足够好了——它不只是存数据的，它是你最被低估的基础设施。

少一个组件，多一分安宁。这话做运维的应该都懂。

# Karpathy的autoresearch：单GPU实现AI自动研究，开源前沿项目解析

2026年3月，Andrej Karpathy在GitHub上扔出了一个叫 `autoresearch` 的项目。简单说就是：给AI agent一台带GPU的机器，让它自己跑研究实验——改代码、训练、看结果、保留或丢弃、循环往复。你睡觉去了，早上起来看实验日志就行。

这个项目现在已经拿了8万多星，热度相当夸张。今天来拆解一下它的技术架构，顺便讲讲怎么在你自己的机器上跑起来。

## 核心设计：三个文件撑起一个研究框架

autoresearch的设计哲学极其克制——整个仓库真正有意义的只有三个文件：

- **`prepare.py`**：数据准备脚本。负责下载训练数据、训练BPE tokenizer，同时提供dataloader和评估函数。这个文件是只读的，agent不能改。
- **`train.py`**：这是唯一被agent修改的文件。完整的GPT模型定义、优化器（Muon + AdamW）、训练循环全在这里。模型架构、超参、batch size、优化器策略——什么都能改。
- **`program.md`**：给AI agent的指令文档。类似一个"研究组织章程"，告诉agent实验流程、约束条件和记录格式。这个文件由人类编辑和迭代。

训练基于一个简化的单GPU实现版本的nanochat。每次实验固定跑5分钟（wall clock时间，不含启动和编译），评估指标是 **val_bpb**（validation bits per byte），越低越好。这个指标和词表大小无关，所以架构变更的对比是公平的。

## 实验循环：自主研究的闭环

整个自主研究的流程是一个死循环，agent被要求永远不要停下来：

1. 看当前git状态
2. 修改 `train.py`，实现一个实验想法
3. git commit
4. 执行 `uv run train.py > run.log 2>&1`，把输出重定向（避免撑爆agent的上下文窗口）
5. 从日志里grep出 `val_bpb` 和 `peak_vram_mb`
6. 如果跑崩了，看trace尝试修复；修不好就记录crash继续下一个
7. 把结果写进 `results.tsv`（TSV格式，不要提交这个文件到git）
8. val_bpb降低了就保留commit，没降就 `git reset` 回去

设计上有个很聪明的地方：每次实验5分钟，每小时约12次实验，睡一觉大概能跑100次。5分钟的固定时间预算让不同架构的实验直接可比——不管agent把模型改多大或多小，训练时间都是一样的。

## 技术细节：模型架构

`train.py` 里的模型是标准的GPT架构，但用了一些现代tricks：

- **RMSNorm**（而非LayerNorm）
- **RoPE旋转位置编码**
- **GQA**（Grouped Query Attention），`n_kv_head` 可以小于 `n_head`
- **Value Embedding**：每隔一层交替使用，来自最近的一些研究工作
- **Flash Attention 3**：Hopper GPU用varunneal的实现，非Hopper用kernels-community的fallback
- **窗口注意力**：默认模式是"SSSL"，即三个局部窗口加一个全局注意力层

优化器用的是 **Muon + AdamW**的组合，这也是Karpathy近期力推的训练策略。

## 为什么这个项目有意思

从技术上看，autoresearch解决了一个很实际的问题：**怎么把AI agent真正用到工程研究里，而不是只做代码补全**。它不是让AI帮你写代码，而是让AI自己做实验、评估结果、决定下一步。这是一个完整的自动化研究闭环。

从运维角度看，这个项目的部署门槛极低。你只需要一台带NVIDIA GPU（测试环境是H100）的机器、Python 3.10+和uv包管理器。安装依赖一条 `uv sync`，数据准备一条 `uv run prepare.py`，然后启动你的coding agent（Claude、Codex之类的），指向 `program.md` 就完事了。

Karpathy自己也说了，设计原则就是"单GPU、单文件、单指标"。没有分布式训练，没有复杂的配置系统，没有任何花哨的东西。这种极简主义反而让项目的可复现性极好。

## 复现指南：在自己的机器上跑起来

**硬件要求**：单张NVIDIA GPU，H100最佳，其他卡也能跑但速度不同。

**环境准备**：

```bash
# 安装uv包管理器
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv sync

# 首次运行：下载数据+训练tokenizer（约2分钟）
uv run prepare.py

# 手动跑一次实验验证环境（约5分钟）
uv run train.py
```

如果上面都跑通了，就可以进入自主研究模式了。启动你的coding agent（确保关掉了所有权限限制），然后跟它说：

> Hi have a look at program.md and let's kick off a new experiment! let's do the setup first.

agent会自动创建git分支、读取上下文文件、开始实验循环。

**小显存机器的适配建议**：如果你想在MacBook或者小显存的卡上跑，Karpathy在README里给了具体的调参建议——换TinyStories数据集、降低 `vocab_size`、缩短 `MAX_SEQ_LEN`、减少 `DEPTH` 等。社区已经有了macOS、Windows、AMD的fork。

## 写在最后

autoresearch真正有意思的地方不是它用了多厉害的技术，而是它提出了一个范式：**把研究过程本身变成一个可以被AI agent执行的任务**。`program.md` 就是这个范式的灵魂——你用Markdown定义研究流程，agent按照流程自主执行。

8万星不是白来的。在AI coding工具已经满天飞的2026年，这个项目指向了一个更远的方向：AI不只是帮你写代码，它能自己做研究。

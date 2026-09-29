# SceneBuilder

本分支在原有有线流程上扩展了正常 WiFi 场景，包含位置/移动、无线设备与队列、信道、
ns-3 导入、统一 Twin 与分析问题。使用方式、测试和已知限制见
[WiFi 第二版说明](docs/wifi_v2.md)。20 节点仅为自建示例，输入拓扑已放入仓库；
生成的场景、Twin、标签、交付包和本机环境不纳入 Git。

WiFi 示例（先按下文初始化 ns-3）：

```bash
python main.py scenes -c configs/wifi_v2_normal_example.yaml
python main.py twin -t origin -c configs/wifi_v2_normal_example_questions.yaml
python main.py questions -t analysis -c configs/wifi_v2_normal_example_questions.yaml
```

以下保留原工程通用命令说明。WiFi 当前只支持正常场景与分析问题，不能直接套用下文的
演化、优化流程；这些原有有线功能保留不变。

SceneBuilder 用于批量构建网络实验数据。它将拓扑与随机配置转换为可供 ns-3 读取的网络
场景，运行内置的 ns-3.48 仿真生成数字孪生体，并从孪生体生成带标签的问题数据。所有
步骤统一通过项目根目录的 `main.py` 执行。

## 运行指令

完整流程依次运行：

```bash
cd /home/lb/STN/SceneBuilder

# 0. 首次克隆后初始化 ns-3（只运行一次）
python main.py initial ./ns-allinone-3.48.tar.bz2

# 1. 生成原始场景输入
python main.py scenes -c configs/example.yaml

# 2. 生成原始 Twin
python main.py twin -t origin

# 3. 生成分析问题
python main.py questions -t analysis -c configs/question_generator.yaml

# 4. 生成演化场景及 Twin
python main.py twin -t evolution

# 5. 生成演化问题
python main.py questions -t evolution -c configs/question_generator.yaml

# 6. 生成优化候选场景及 Twin
python main.py twin -t optimization

# 7. 生成优化问题
python main.py questions -t optimization -c configs/question_generator.yaml

# 8. 按问题划分训练集和测试集
python main.py split -c configs/dataset_split.yaml -r 0.8
```

命令格式为：

```bash
python main.py <模式> [选项]
```

可用模式：

- `initial`：首次克隆后解压、配置并编译 ns-3。
- `scenes`：生成网络场景的原始输入。
- `twin`：生成 `origin`、`evolution` 或 `optimization` Twin。
- `questions`：从已有 Twin 和标签生成问题。
- `split`：按问题模板分别划分训练集和测试集。
- `clean`：清理场景配置对应的已有场景。

### 0. 初始化 ns-3

首次从 Git 克隆项目后，传入 ns-3 压缩包路径：

```bash
python main.py initial ./ns-allinone-3.48.tar.bz2
```

如果省略路径，命令会提示输入：

```bash
python main.py initial
请输入 ns-3 压缩包路径: ./ns-allinone-3.48.tar.bz2
```

命令会自动识别压缩包内的 ns-3 源码根目录，将内容解压到项目的 `ns-3/`，并跳过
Git 仓库中已有的自定义 `scratch/` 和 `contrib/` 文件。解压完成后会自动依次执行：

```bash
./ns3 configure --build-profile=debug --enable-modules=network-scene
./ns3 build
```

任一命令执行失败都会使 `initial` 返回失败。如果 `ns-3/ns3` 已经存在，命令会拒绝
再次初始化，避免混合不同版本的源码。尚未初始化时运行 `scenes` 或 `twin`，程序会
停止并提示先执行 `initial`。

### 1. 生成场景

```bash
python main.py scenes -c configs/example.yaml
```

场景原始输入生成到配置文件的 `output_root`，示例配置对应：

```text
/home/lb/STN/SceneBuilder/generated/origin/input
```

一次生成的场景数量为：

```text
符合 max_topology_nodes 限制的拓扑数量 x scenes_per_topology
```

`scenes` 不会覆盖或清理任何已有内容。只要配置指定的 `output_root` 中已经存在文件
或目录，命令就会立即停止并提示先运行：

```bash
python main.py clean
```

清理后才能重新生成场景。

### 2. 生成 Twin

生成原始 Twin：

```bash
python main.py twin -t origin
```

原始场景输入从 `origin/input/<scene_id>/` 读取，生成的 Twin 扁平保存为
`origin/scenes/<scene_id>.jsonl`，标签保存到对应的
`origin/input/<scene_id>/labels.jsonl`。`origin/scenes` 中只保存 Twin 文件。

Twin 命令不会删除或覆盖已有 Twin、标签、演化派生场景或问题。只要目标输出已经存在，
命令就会停止并提示先清理对应分组，例如 `python main.py clean -o twin -t origin`。旧版
`origin/scenes/<scene_id>/` 中仍有 Twin 时会在迁移前拒绝执行；显式清理 Twin 后，
下一次 `twin -t origin` 才会把保留下来的原始输入迁移到 `origin/input/<scene_id>/`。

生成演化场景及 Twin：

```bash
python main.py twin -t evolution
```

该命令从具有完整原始 Twin 和标签的 origin 场景中随机抽样，读取
`question_generator/templates/evolution.yaml` 中独立定义的 `events`，将事件施加到
复制的静态输入并写入 `evo/input/<scene_id>/`，然后将演化 Twin 写入
`evo/scenes/<scene_id>.jsonl`。当前包含节点、信道和网卡的故障/恢复、数据流负载的
变化以及新增数据流，共八类事件。

负载变化事件按原始 Twin 状态分层抽样。默认
`load_change_saturated_flow_ratio: 0.5`：一半事件随机选择路径经过饱和信道或 NIC
的流，另一半随机选择状态为 normal 且路径没有饱和信道/NIC 的流。每个 origin 场景在
同一事件类型中最多使用一次，所选分类会写入事件元数据的 `source_flow_class`。新的需求
值等于原需求乘以 `load_change_multiplier_range` 中随机采样的倍率，默认范围为 `[0.0, 3.0]`；
问题直接显示计算后的 Mbps，不再显示倍率。

生成优化场景 Twin：

```bash
python main.py twin -t optimization
```

该命令从具有完整 Twin 和标签的 origin 场景中构造优化候选组，不因存在物理故障而跳过
整个场景。每组包含一个不施加动作的上下文场景和至少两个候选动作场景；上下文 Twin 直接
复用对应 origin Twin，候选场景保留相同的背景故障并分别运行 ns-3，互不叠加动作。扩容
候选必须是当前仍可运行的信道，路由候选的完整结果路径必须避开已禁用的节点、信道和网卡。
故障修复使用正常 origin 作为共同背景，不生成双故障 Twin，而是生成两个只包含一个剩余
故障的候选 Twin。
目前实现：

- `TO0001` 信道扩容：选择某个节点上至少两条有流量经过的相邻信道，把每条候选信道分别
  扩容到问题中相同的绝对容量，比较全网所有接收端数据流吞吐量之和。
- `TO0002` 路由调整：固定一个当前转发节点和一个目的节点，从至少两个直连且经由正常
  路径可达的候选下一跳中选择；每个候选只替换路由矩阵中的一个表项，比较所有发往该目的
  节点的数据流的接收吞吐量之和。其余路由表项和背景故障全部保持不变。原路径正常时选择
  拥塞场景，原路径故障时允许候选路由绕开故障。
- `TO0003/TO0004`：复用 TO0001 的信道扩容候选 Twin，分别优化全网所有流的包加权平均
  端到端延迟和聚合丢包率。
- `TO0005/TO0006`：复用 TO0002 的单路由表项候选 Twin，分别优化发往指定目的节点的流的
  包加权平均端到端延迟和聚合丢包率。
- `TO0007/TO0008/TO0009`：从正常 origin 中选择两个互不属于同一故障域、影响的数据流集合
  不完全相同且位于数据流作用范围内的故障实体，并将故障统一设为 `disabled`。候选一只注入
  故障 B，表示修复 A 后的状态；候选二只注入故障 A，表示修复 B 后的状态，分别比较全网
  吞吐量、全网包加权平均延迟和全网聚合丢包率。

候选动作、目标流集合和问题占位符都记录在 `opt/input/<scene_id>/metadata.json`。该命令
会创建派生输入，因此不支持 `--dry-run`；为了保证候选间可比，也不允许用 `--stop-time`
覆盖仿真时长。

### 3. 生成问题

分析问题：

```bash
python main.py questions -t analysis -c configs/question_generator.yaml
```

演化问题：

```bash
python main.py questions -t evolution -c configs/question_generator.yaml
```

优化问题：

```bash
python main.py questions -t optimization -c configs/question_generator.yaml
```

`-c` 默认使用 `configs/question_generator.yaml`。也可以覆盖场景数据根目录：

```bash
python main.py questions -t analysis \
  -c configs/question_generator.yaml \
  --scene-root generated
```

分析问题允许使用部分完成的 origin Twin；命令会报告已使用和跳过的场景数。只有没有
任何完整场景时才会要求先运行 `python main.py twin -t origin`。演化问题要求先完成
`python main.py twin -t evolution`，通过比较变更前后的 Twin 生成实际满足目标变化的问题。
普通演化模板针对每个模板及目标答案打乱对应事件的演化 Twin，逐个尝试生成问题；
状态预测矩阵则按模板总数从具有双侧状态证据的候选中采样实际标签，不为某个事件无法产生的
状态制造伪样本；事件或目标有效但状态/指标证据不足时改为生成 `unknown`。每个 Twin 对同一
模板最多贡献一道题，达到配置数量后立即停止。信道扩容和路由调整问题比较同一候选组的
上下文 Twin 和全部候选 Twin。最优候选相对原场景存在超过阈值的正向提升，并且领先第二名
超过胜出阈值时生成其实体 ID，否则生成 `unknown`。故障修复问题只比较两个单故障候选 Twin
的绝对结果，不使用正常 context 作为改善基准；两个修复结果没有形成唯一最优时同样生成
`unknown`。
TO0001/TO0003/TO0004 的标签是 `Cxxxx` 信道 ID，TO0002/TO0005/TO0006 的标签是问题中
列出的 `Nxxxx` 下一跳节点 ID，TO0007/TO0008/TO0009 的标签是应当修复的节点、信道或
接口 ID。
问题的 `scene_name` 指向未施加动作的上下文 Twin，候选 Twin 只用于离线计算标签。

问题生成不会删除或覆盖已有问题列表。目标问题输出已经存在时，命令会立即
停止并提示先运行：

```bash
python main.py clean -o questions
```

问题模板使用稳定的 `template_id`：分析、演化和优化模板分别使用 `TA`、`TE`、`TO`
前缀。每道具体问题使用独立的 `question_id`，格式为 `Q00000001`；同一个模板可以生成
多道具体问题。

### 4. 划分训练集和测试集

划分比例必须在每次运行时显式输入。例如按 80%/20% 划分：

```bash
python main.py split \
  -c configs/dataset_split.yaml \
  -r 0.8
```

配置文件默认是 `configs/dataset_split.yaml`，可以省略 `-c`，但不能省略比例：

```bash
python main.py split -r 0.8
```

`configs/dataset_split.yaml` 配置：

- `question_config`：问题列表和生成场景根目录所使用的问题配置。
- `train_output_root`：训练集输出目录。
- `test_output_root`：测试集输出目录。
- `template_output_root`：训练集和测试集共用的问题模板目录。
- `seed`：可复现划分所使用的随机种子。

`-r` 是 `--train-ratio` 的简写，必须在 `0` 和 `1` 之间；配置文件不提供默认划分比例。

`train_output_root`、`test_output_root` 和 `template_output_root` 必须都不存在或为空。任一输出目录非空时，
`split` 会在创建临时数据前直接停止，不会删除或覆盖已有数据集；需要先显式清空对应目录
或在配置中改用新的输出路径。

划分以问题为单位，并在每个任务内按 `template_id` 分层处理，不按场景整体划分。同一
场景可以因为不同问题同时出现在训练集和测试集中。输出按
`analysis/evolution/optimization` 分任务，每个任务目录包含该任务的问题列表和
`scenes/`；其中 `scenes/` 直接保存问题涉及的 `<scene_id>.jsonl` Twin，不创建场景子目录，
也不复制原始输入、标签或其他文件。某个训练或测试分片中没有对应任务的问题时，不创建
该任务目录。三类任务模板只复制一份，集中保存在数据集根目录的 `question_template/` 中，
不会在 `train/` 和 `test/` 下重复保存。

默认配置输出到：

```text
/home/STN-Runtime/datasets/STN_tasks/
├── question_template/
│   ├── analysis.yaml
│   ├── evolution.yaml
│   └── optimization.yaml
├── train/
│   ├── analysis/
│   ├── evolution/
│   └── optimization/
└── test/
    ├── analysis/
    ├── evolution/
    └── optimization/
```

### 5. 分层清理

`-o/--object` 指定清理对象 `scenes`、`twin` 或 `questions`。`twin` 和 `questions`
可以再用 `-t/--type` 指定内部类型；省略 `-t` 会清理该对象下的全部类型。`scenes`
没有下级类型，不能搭配 `-t`。

清理全部生成内容：

```bash
python main.py clean
python main.py clean -o scenes
```

两个命令行为相同：使用默认的 `configs/example.yaml`，清空其 `output_root` 中的场景
输入、Twin、标签、演化派生输入和问题输出。场景是最上游数据，因此 `-o scenes` 不会
保留下游产物。使用其他场景配置时：

```bash
python main.py clean -o scenes -c configs/example.yaml
```

只清理 Twin 层及其下游问题：

```bash
python main.py clean -o twin
```

该命令保留 `origin/input` 中的原始场景输入，删除 origin Twin 和标签、全部
`evo/input`、`evo/scenes`、`opt/input` 与 `opt/scenes`，并删除所有问题列表。
使用其他问题配置时可增加 `-c <question_config>`。

也可以只清理指定的 Twin 分组及其对应问题：

```bash
python main.py clean -o twin -t origin
python main.py clean -o twin -t evolution
python main.py clean -o twin -t optimization
```

- `clean -o twin -t origin`：保留 `origin/input` 原始输入，删除 origin Twin、标签和 analysis
  问题。
- `clean -o twin -t evolution`：删除全部 `evo/input`、`evo/scenes` 和 evolution 问题，不影响
  origin 和 opt。
- `clean -o twin -t optimization`：删除全部 `opt/input`、`opt/scenes` 和 optimization 问题，
  不影响 origin 和 evo。优化输入是从 origin 自动派生的，重新生成 Twin 时会重新构造。

三个分组相互独立；清理 origin 不会自动删除已有 evolution，清理 evolution 也不会删除
origin。

只清理问题：

```bash
python main.py clean -o questions
```

该命令只删除分析、演化和优化问题列表，以及兼容旧目录时发现的局部问题文件，
不删除场景输入、Twin 或标签。

也可以只清理一种问题：

```bash
python main.py clean -o questions -t analysis
python main.py clean -o questions -t evolution
python main.py clean -o questions -t optimization
```

三个命令分别只删除 analysis、evolution 或 optimization 的问题列表，
不会删除其他类型的问题，也不会删除任何场景输入、Twin 或标签。

所有生成命令都不会自动调用这些清理操作；清理只能由上述显式命令触发。

查看帮助：

```bash
python main.py --help
python main.py initial --help
python main.py twin --help
python main.py questions --help
python main.py split --help
python main.py clean --help
```

## 环境准备

先安装 Python 依赖以及 ns-3 所需的编译工具：

```bash
cd /home/lb/STN/SceneBuilder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

然后按“初始化 ns-3”一节运行 `initial`；该命令会自动解压、配置并完整编译 ns-3，
无需再手动执行 `./ns3 configure` 或 `./ns3 build`。

之后返回项目根目录运行 SceneBuilder：

```bash
cd /home/lb/STN/SceneBuilder
```

## 输出结构

```text
generated/
├── origin/
│   ├── analysis_questions.jsonl
│   ├── input/
│   │   └── <original_scene_id>/
│   │       ├── metadata.json
│   │       ├── nodes.csv
│   │       ├── channels.csv
│   │       ├── nics.csv
│   │       ├── routing_matrix.csv
│   │       ├── traffic.jsonl
│   │       └── labels.jsonl
│   └── scenes/
│       └── <original_scene_id>.jsonl
├── evo/
│   ├── evolution_questions.jsonl
│   ├── input/
│   │   └── <evolved_scene_id>/
│   │       └── 与原场景相同的输入文件及 labels.jsonl
│   └── scenes/
│       └── <evolved_scene_id>.jsonl
└── opt/
    ├── optimization_questions.jsonl
    ├── input/
    │   ├── <optimization_context_scene_id>/
    │   │   └── 未施加动作的上下文输入、优化候选元数据及 labels.jsonl
    │   └── <optimization_candidate_scene_id>/
    │       └── 仅施加一个候选动作的输入及 labels.jsonl
    └── scenes/
        ├── <optimization_context_scene_id>.jsonl
        └── <optimization_candidate_scene_id>.jsonl
```

普通场景生成始终写入 `origin/input`，`twin -t origin` 将 Twin 写入 `origin/scenes`。
`twin -t evolution` 基于原场景创建新输入并分别写入 `evo/input` 和 `evo/scenes`；
`twin -t optimization` 创建上下文和候选输入并写入 `opt/input` 与 `opt/scenes`。三个
`scenes/` 目录都只保存以场景名命名的 Twin JSONL 文件。演化场景名末尾使用事件场景 ID，例如
`example_id2001_York_t2s_evo_E00000001`。三个目录中的数字场景 ID 共用同一编号空间。

场景输入文件的作用：

- `metadata.json`：场景来源、随机种子、生成规则和数量统计。
- `nodes.csv`：节点及其基础状态。
- `channels.csv`：节点之间的信道、原始容量和基础状态。
- `nics.csv`：独立网卡实体、所属节点、信道、队列配置和基础状态。
- `routing_matrix.csv`：物理故障发生前，按初始完整拓扑计算出的静态出口接口索引；初始拓扑不可达时为 `-1`。后续物理故障不会重算或改写该文件。
- `traffic.jsonl`：场景中的数据流及其需求和流量模型。

`scenes/<scene_id>.jsonl` 是 ns-3 输出的数字孪生体。每行表示一个实体，例如节点、
网卡、信道或数据流，包含实体 ID、属性和关系，不包含标签。

数据流的 `demand_mbps` 和 `throughput_mbps` 都采用应用层有效载荷口径；
`throughput_mbps` 不包含 IPv4 和 UDP 头部。信道和网卡的带宽统计仍表示网络实际承载的
数据量，因此保留相应协议开销。

`input/<scene_id>/labels.jsonl` 独立保存能够由分析问题生成器直接使用的答案。节点、网卡、信道和数据流
状态分别写在 `node_state`、`nic_state`、`channel_state`、`data_flow_state` 行中，每行的
`label` 都是由 `{entity_id, label}` 构成的列表。节点状态只包含 `normal` 或
`disabled`。路由项是否可用不改变节点状态。没有对应问题模板的全网状态不写入
`labels.jsonl`。

四类实体状态行始终写出。其余条件型标签只有在当前场景至少存在一个有效答案时才写出；
不会保留 `label: []` 的空行。缺少某一条件型标签行表示该场景不能为对应题型提供候选，
不是 Twin 文件不完整。

`bottleneck` 行保存可确认的流瓶颈，格式为 `{data_flow_id, channel_id}`。仅当一条流的
完整路径至少包含两条信道、路径上恰好有一个 `saturated` 信道且其余信道全部为 `normal`
时才写入。单跳流不会产生瓶颈标签。

`data_flow_congestion_pattern` 行保存流路径的拥塞模式。路径链路全部为 `normal` 或 `saturated` 且恰好一条链路饱和时标记为 `single_channel_bottleneck`；至少两条链路饱和时标记为 `multi_channel_saturation`。路径无饱和链路，或包含 `disabled/degraded` 链路时不生成该标签。

信道的 `relations.carries` 保存经过该信道的数据流及其实际带宽占用，不再只是流 ID
列表。每个元素只包含 `data_flow_id` 和 `bandwidth_mbps`。带宽从该流在当前信道发送端
网卡的 `PhyTxBegin` 统计，只有真正开始在物理信道上传输的数据包才计入；队列中提前丢弃
的数据包不占用信道带宽。同一方向所有 `carries.bandwidth_mbps` 之和就是该方向的当前
吞吐量；每条流在该信道上的方向由流的 `path_nodes` 和 `path_channels` 确定。

`channel_saturation_cause` 行保存饱和信道的流量构成原因，格式为 `{channel_id, label}`。对经过该信道的全部数据流按 `demand_mbps` 求和；若最大流的需求严格大于其余流需求之和，标记为 `single_large_flow`，否则标记为 `multiple_flow_aggregation`。非饱和信道或没有可确认经过流的信道不生成该标签。

`channel_unavailability_cause` 和 `nic_unavailability_cause` 分别保存不可用信道与网卡的
故障来源，元素格式均为 `{entity_id, label}`。连接节点故障使用
`connected_node_fault`；信道自身或任一端网卡故障统一使用
`channel_or_interface_fault`。节点侧与信道侧故障同时存在、实体并非 `disabled`，或来源
不能唯一确定时不写入该标签。

`data_flow_bandwidth_constraint` 行保存数据流路径的带宽约束，并且只使用
`insufficient_channel_capacity` 和 `traffic_congestion` 两种标签。路径上任一方向当前
吞吐量达到原始容量 95% 的信道属于饱和瓶颈；存在多个时，以原始容量最小的信道为瓶颈
信道。瓶颈信道的原始容量严格小于流需求时标记为
`insufficient_channel_capacity`，否则标记为 `traffic_congestion`。信道是否退化不参与
这个二分类。

链路实体只公开 `properties.original_capacity_mbps` 和 `properties.delay_ms`。信道的两个
方向不再保存单独的吞吐量属性，而是根据 `carries` 中各流的实际带宽及其完整路径进行
分组求和；信道状态判断取两个方向中的较大总和。Twin 不公开当前吞吐量汇总、有效容量、
可用带宽或利用率。网卡的
`tx_rate_mbps`、`tx_packets` 是该网卡在本地 IPv4 层实际尝试发往链路的流量，
`rx_rate_mbps`、`rx_packets` 是该网卡从链路实际收到的流量；因此可逐跳比较发送端与对端
接收端，判断路径中任意一条链路是否造成传输损失。

`data_flow_failure_cause` 行保存导致数据流 `failed` 的唯一物理故障实体，格式为
`{data_flow_id, entity_id}`，其中 `entity_id` 只能是节点 ID 或链路 ID。路径链路的网卡为
`disabled` 时，根因统一归并为所属链路 ID，不输出网卡 ID。只有目标流路径至少包含两条
信道，且去重后恰好只有一个故障节点或链路时才写入；单跳流、多故障或没有明确根因时不
产生该标签。

分析问题的实体状态候选仅限数据流覆盖范围：节点必须出现在至少一条流的路径中，链路必须属于至少一条流的 `path_channels`，网卡必须属于这些路径链路。未被任何流经过的实体不会被抽取。数据流实体通过 `path_nodes` 和 `path_channels` 显式保存故障前静态路由确定的完整路径；物理故障不会使这两个字段缩短、清空或改为备用路径。

分析问题采用“标签或推导二选一”的规则：只要答案已经写入 `labels.jsonl`，问题生成器就
直接使用，不再从对应的 Twin 文件重复推导或交叉校验。当前十一类分析标签与模板一一对应：

| 标签类型 | 问题模板 |
| --- | --- |
| `node_state` | `TA0001` |
| `channel_state` | `TA0002` |
| `nic_state` | `TA0003` |
| `data_flow_state` | `TA0004` |
| `data_flow_bandwidth_constraint` | `TA0005` |
| `data_flow_congestion_pattern` | `TA0006` |
| `channel_saturation_cause` | `TA0007` |
| `bottleneck` | `TA0008` |
| `data_flow_failure_cause` | `TA0009` |
| `channel_unavailability_cause` | `TA0010` |
| `nic_unavailability_cause` | `TA0011` |

问题生成器仍会检查标签引用的实体是否存在，并对实体状态题和不可用原因题应用数据流覆盖
范围筛选；这些检查只决定候选能否用于模板，不会重新计算答案。必须通过比较两个 Twin
才能得到的演化问题标签，例如吞吐量、丢包数、时延或状态变化，不写入 `labels.jsonl`，
而是在生成演化问题时按需计算。

若某类标签的场景数或答案分布不足以达到配置数量，生成器保留已生成的问题，并在命令行
报告实际数量。

三类问题模板共用文件顶层的 `unknown_answer` 策略，标签固定为 `unknown`。问题及目标必须
先满足对应模板的结构和语义前提；只有可用 Twin 证据缺失、相互矛盾或不能支持唯一答案时，
才生成 `label: "unknown"`。物理或逻辑上不可能成立的问题不会借此恢复为候选，损坏的场景
文件也不会作为 `unknown` 样本。每条 `unknown` 记录还包含 `evidence`，其中
`status: "insufficient"`、`reason` 和 `required_evidence` 分别说明证据状态、无法判断的直接
原因以及要得到确定答案仍缺少的证据。已有问题文件不会被自动重写，重新运行 `questions`
后才会产生新标签。

数据流状态的判断优先级为 `failed > unstable > degraded > normal`：无统计、未发送或未接收数据时为 `failed`；成功接收但有丢包时为 `unstable`；无丢包但吞吐量低于需求带宽的 95% 时为 `degraded`；其余情况为 `normal`。

问题文件的位置由 `configs/question_generator.yaml` 中各类别的 `output_file` 决定。
默认分析问题写入 `generated/origin/analysis_questions.jsonl`。

演化问题的总列表默认写入
`generated/evo/evolution_questions.jsonl`。每条记录除通用字段外，还包含：

- `original_scene_id`：变更前的原场景 ID。
- `evolved_scene_id`：基于原场景生成的新场景 ID。
- `scene_name`：为兼容通用问题读取逻辑，值与 `evolved_scene_id` 相同。

不再为每个场景单独写问题文件；每种任务只保留由 `output_file` 指定的全局问题列表。
演化场景的 `input/<scene_id>/metadata.json` 会记录来源场景、事件场景 ID 和静态变更
内容。所有问题类型都只在至少生成一道问题时创建 JSONL；问题列表为空时不会保留空文件。

## 配置说明

### 场景配置

场景配置示例为 `configs/example.yaml`，主要控制：

- `output_root`：场景输出目录。
- `seed`：随机种子。
- `scenes_per_topology`：每个符合条件的拓扑生成多少个场景。
- `max_topology_nodes`：允许参与生成的最大拓扑节点数。
- `scene_duration`：业务应用的运行时长。应用默认在第 1 秒启动，因此绝对仿真停止时刻为
  `1s + scene_duration`。
- `topology_sources`：Topology Zoo 或 BRITE 拓扑来源。
- `fault_generation`：全网正常、单故障和双故障的抽样概率，以及链路、网卡物理故障的状态分布。被抽中的节点物理故障固定为崩溃 `disabled`。
- `link_generation`、`nics`、`routing`：信道、网卡、队列和路由生成规则。
- `traffic_matrix`、`flow_feature`：流数量、需求大小和流量模型。

配置中的相对路径均相对于该 YAML 文件所在目录解析。

路由表先在无故障的初始完整拓扑上按加权最短路径生成，随后才抽取和施加节点、链路或网卡物理故障。故障发生后不重算路由，也不自动切换备用路径；因此静态路径可能继续指向已经停用的节点、链路或网卡。

路由表项是否仍然可用不会改变节点状态：节点自身未崩溃时保持 `normal`，只有节点自身发生
物理故障时才是 `disabled`。不可用的静态路由仍可用于分析流量失败原因或提出路由表修改，
但不再派生额外的节点状态。网卡故障在根因标签中归并为所属链路故障。

### 问题配置

问题配置示例为 `configs/question_generator.yaml`，主要控制：

- `scenes_root`：包含场景及孪生体的目录。
- `seed`：问题实体选择的随机种子。
- `questions_per_question`：每条问题模板期望生成的总数量；生成器会把 `unknown` 与该模板的
  其他答案目标一起纳入数量分配。
- `template_file`：该类问题使用的模板文件。
- `output_file`：生成问题的 JSONL 输出位置。
- `enabled`：是否启用对应的问题类别。

问题生成阶段不会把模板复制到 `generated/`。运行 `split` 时，三类 `template_file` 才会
分别复制为 `STN_tasks/question_template/analysis.yaml`、`evolution.yaml` 和
`optimization.yaml`，供训练集和测试集共用。分析模板通过结构化 `answer` 声明答案类型，演化模板直接用
`answer: [value1, value2]` 声明允许的标签；优化问题的答案格式由对应模板 ID 的生成规则确定。
三个文件顶层都必须声明同一份 `unknown_answer` 策略；加载器会将该标签加入文件中的每条
问题模板，无需在每个 `answer` 中重复书写。

模板文件是 `schema_version: 1` 的 YAML。分析和演化任务的 `templates` 每项包含唯一的
`id`、问题文本 `question` 和 `answer`；演化模板的 `answer` 是标签列表，不再重复声明
`type: enum` 和 `values`。优化任务的每项包含 `id`、`strategy` 和
`question`，答案格式由对应的优化问题生成规则确定。三类模板 ID 分别使用 `TA`、`TE`、
`TO` 前缀，后接四位数字；演化模板当前按文件顺序从 `TE0001` 连续编号。问题生成器根据
模板 ID 选择对应生成规则。
演化模板文件的 `events` 与 `templates` 相互独立：`events` 供 `twin -t evolution` 构造演化场景
和 Twin，`templates` 供 `questions -t evolution` 根据前后 Twin 的证据生成具体问题。
每个事件条目包含 `id`、`entity_type` 和非空的 `description`；事件 ID 是生成行为的唯一
标识，具体变更方式由代码中的事件语义映射确定，描述用于说明该事件对场景的修改语义。
`TE0026` 至 `TE0057` 组成 8 类事件与 node/channel/nic/data_flow 四类目标实体的
完整状态预测矩阵，答案标签从分析状态标签中按事件语义取理论可达子集。例如，信道故障不会
使节点变为 `disabled`，恢复、负载变化和新增流也不会产生新的物理 `disabled` 状态；当前事件
规则不会在演化后保留或创建 `degraded` 信道。此类模板不为物理上不可能出现的状态强行凑平衡
样本；事件实体和已有目标必须在原始 Twin 中有状态证据，目标在演化后 Twin 中的状态
也必须能由可观测属性重新推导且与标签一致。新增流不存在原始状态，因此改为检查其端点存在、
当前路由证据可查询，并要求新增后的流状态满足同样的证据一致性。

优化模板文件的 `strategies` 声明 `routing_adjustment`、`channel_expansion` 和
`fault_repair` 三种策略。`routing_adjustment` 只修改指定转发节点面向指定目的节点的一个
路由表项，直接影响当前经过该转发节点的相关流；优化目标仍统计所有发往该目的节点的流。
每个优化模板通过自身的 `strategy` 字段声明所属策略。模板加载器要求
三种策略同时存在，并保证每个 TO 模板恰好属于一种已声明策略。生成问题时必须把候选下一跳、
候选扩容信道或候选修复实体直接列在问题中；候选不足两个、没有
真实改善、指标证据不完整或存在并列最优时无法确定唯一答案，并生成带证据说明的
`unknown`；候选不足两个等不满足问题结构的场景仍不生成。故障修复使用单独的双候选绝对
指标比较规则。
`TO0007/TO0008/TO0009` 使用 `fault_repair`，分别优化全网吞吐量、全网包加权平均延迟和
全网聚合丢包率。三个模板使用同一组两个单故障候选：只保留故障 B 的 Twin 表示修复 A，
只保留故障 A 的 Twin 表示修复 B。正常 context 只提供共同拓扑、路由和流量背景，不生成
双故障 Twin，也不引入第三个故障候选。
信道扩容的目标容量由 `options.channel_expansion_capacity_candidates_mbps` 配置为一组
离散 Mbps 数值。针对一个锚定节点，只从严格大于其全部候选信道当前容量的配置值中随机
选择一个，并将所有候选分别扩容到同一个目标容量；没有合格配置值时跳过该节点。
`channel_expansion_max_candidates` 限制一道 TO0001 中的候选信道数。
`routing_next_hop_max_candidates` 限制一道 TO0002 中的候选下一跳数量，
`routing_max_destination_flows` 限制被选目的节点的入流数量，控制聚合目标的推理范围。
`fault_repair_scenarios` 单独设置故障修复候选组数量；默认配置为 `300`，用于覆盖候选指标
并列或无法计算而不能生成问题的情况。
`scenarios_per_template` 设置扩容和路由两类优化动作在仿真前构造的候选组数；同一组信道扩容 Twin
供 TO0001/TO0003/TO0004 共用，同一组路由调整 Twin 供 TO0002/TO0005/TO0006 共用，
同一组故障修复 Twin 供 TO0007/TO0008/TO0009 共用，不会为每个指标重复仿真。实际问题数
仍由 `questions_per_question` 限制。

吞吐量使用 `throughput_improvement_tolerance_mbps` 和 `winner_margin_mbps` 作为相对基准的
最小提升及第一名领先量。延迟使用 `delay_improvement_tolerance_ms` 和
`delay_winner_margin_ms`；聚合延迟按 `Σ(average_delay_ms × rx_packets) / Σrx_packets`
计算，零接收流不贡献接收包或延迟，但流集合整体必须至少收到一个包。延迟题通过
`delay_packet_loss_rate_tolerance` 限制候选相对基准的聚合丢包率增加，已发送但零接收流的
影响也会计入该丢包率，避免以丢弃更多流量换取表面上的低延迟。丢包率按固定流集合的
`Σlost_packets / Σtx_packets` 计算，使用
`packet_loss_rate_improvement_tolerance` 和 `packet_loss_rate_winner_margin` 过滤无真实改善
或并列最优的候选组。
故障修复不使用相对正常 context 的改善阈值，只使用对应的 `winner_margin` 判断两个候选是否
存在唯一最优。TO0008 还要求最低延迟候选的聚合丢包率不能比另一个修复候选高出
`delay_packet_loss_rate_tolerance`。

演化类的 `options.scenes_per_event` 使用一个正整数控制每类事件生成多少个场景；例如设置
为 `3` 时，八类事件各生成 3 个场景，最多生成 24 个演化场景。该区域还可配置负载变化的
`load_change_multiplier_range` 倍率范围、新增流的
`flow_addition_demand_mbps_range` 需求带宽范围，以及优先选择事件相关目标的概率。同一事件
类型内部从符合条件的原场景中随机不放回抽取；不同事件类型彼此独立，可以复用同一个
原场景，前面的事件不会消耗后续事件的候选场景。
演化问题比较吞吐量、时延和信道承载带宽等连续数值时，变化绝对值不超过原值的 `1%`
判定为 `unchanged`。丢包问题比较 `lost_packets / tx_packets` 得到的丢包率，而不是丢包总数；
任一场景的 `tx_packets` 为 `0` 时不生成该问题。丢包率变化绝对值不超过
`packet_loss_rate_change_threshold` 时判定为 `unchanged`，默认阈值为 `0.01`，即一个百分
点。故障事件只在目标流仍能发送、故障移除了同方向共享瓶颈上的竞争流量时，才允许生成
`decrease`。数据流在变更前或变更后没有成功接收数据包时，`average_delay_ms` 没有可比较
意义，生成器不会用结果文件中的占位值 `0` 生成时延变化问题。每个模板的答案空间只保留
当前静态路由、固定流量模型和对应事件机制能够产生的变化方向。
当前已经实现分析类、57 个演化模板（含 32 个状态预测模板），以及 TO0001 至 TO0009
九个优化问题的场景与问题生成。

## 常用选项

- `--stop-time <秒>`：以绝对仿真时刻覆盖默认停止时刻；必须晚于应用启动时刻。
- `--progress-interval <秒>`：设置 ns-3 仿真进度报告间隔，`0` 表示关闭。
- `--no-build`：跳过运行前的显式编译步骤。
- `--continue-on-error`：单个场景失败后继续处理其他场景。
- `--dry-run`：只打印将执行的 ns-3 命令。`twin -t evolution` 和
  `twin -t optimization` 会创建派生场景，因此不支持该选项。
- `questions --scene-root <路径>`：覆盖问题配置中的孪生体场景目录。

`scaleFactor` 会按相同比例缩小仿真中的信道容量、流量需求和队列包数。Twin 中的速率会
恢复到原网络口径，`queue_size_packets` 也仍表示原网络的队列容量；队列当前包数按队列
占用比例恢复。队列至少保留一个仿真包。

`twin -t evolution` 和 `twin -t optimization` 不支持 `--stop-time`。变更前后或不同候选
Twin 必须使用相同的场景仿真时长，才能直接比较吞吐量、丢包数和平均时延。

## 当前约定

- 运行时事件功能当前处于禁用状态。场景表示一个固定网络状态，ns-3 不会在仿真途中注入事件。
- `twin -t evolution` 构造变更前和变更后的独立场景；演化问题命令只比较已经生成的两个 Twin。
- `twin -t optimization` 为每个问题构造一个上下文场景和多个单动作候选场景；优化问题
  命令用候选场景计算标签，只把上下文 Twin 暴露给答题端。
- 每个场景生成一个 `scenes/<scene_id>.jsonl` Twin 和一个
  `input/<scene_id>/labels.jsonl` 标签文件。
- 场景生成和 ns-3 仿真均串行执行，避免同时运行多个大规模仿真任务。

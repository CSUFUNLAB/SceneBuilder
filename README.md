# SceneBuilder

SceneBuilder 用于批量构建网络实验数据。它将拓扑与随机配置转换为可供 ns-3 读取的网络
场景，运行内置的 ns-3.44 仿真生成数字孪生体，并从孪生体生成带标签的问题数据。所有
步骤统一通过项目根目录的 `main.py` 执行。

## 运行指令

完整流程依次运行：

```bash
cd /home/SceneBuilder

# 1. 生成原始场景输入
python main.py generate -c configs/example.yaml

# 2. 生成原始 Twin
python main.py twin origin

# 3. 生成分析问题
python main.py questions -t analysis -c configs/question_generator.yaml

# 4. 生成演化场景及 Twin
python main.py twin evo

# 5. 生成演化问题
python main.py questions -t evolution -c configs/question_generator.yaml

# 6. 按问题划分训练集和测试集
python main.py split -c configs/dataset_split.yaml -r 0.8
```

命令格式为：

```bash
python main.py <模式> [选项]
```

可用模式：

- `generate`：生成网络场景的原始输入。
- `twin`：生成 `origin`、`evo` 或 `opt` Twin。
- `questions`：从已有 Twin 和标签生成问题。
- `split`：按问题模板分别划分训练集和测试集。
- `clean`：清理场景配置对应的已有场景。

### 1. 生成场景

```bash
python main.py generate -c configs/example.yaml
```

场景原始输入生成到配置文件的 `output_root`，示例配置对应：

```text
/home/SceneBuilder/generated_scenes/origin/input
```

一次生成的场景数量为：

```text
符合 max_topology_nodes 限制的拓扑数量 x scenes_per_topology
```

`generate` 不会覆盖或清理任何已有内容。只要配置指定的 `output_root` 中已经存在文件
或目录，命令就会立即停止并提示先运行：

```bash
python main.py clean
```

清理后才能重新生成场景。

### 2. 生成 Twin

生成原始 Twin：

```bash
python main.py twin origin
```

原始场景输入从 `origin/input/<scene_id>/` 读取，生成的 Twin 扁平保存为
`origin/scenes/<scene_id>.jsonl`，标签保存到对应的
`origin/input/<scene_id>/labels.jsonl`。`origin/scenes` 中只保存 Twin 文件。

Twin 命令不会删除或覆盖已有 Twin、标签、演化派生场景或问题。只要目标输出已经存在，
命令就会停止并提示先运行 `python main.py clean twin`。旧版
`origin/scenes/<scene_id>/` 中仍有 Twin 时会在迁移前拒绝执行；显式清理 Twin 后，
下一次 `twin origin` 才会把保留下来的原始输入迁移到 `origin/input/<scene_id>/`。

生成演化场景及 Twin：

```bash
python main.py twin evo
```

该命令从具有完整原始 Twin 和标签的 origin 场景中随机抽样，读取
`question_generator/templates/evolution.yaml` 中独立定义的 `events`，将事件施加到
复制的静态输入并写入 `evo/input/<scene_id>/`，然后将演化 Twin 写入
`evo/scenes/<scene_id>.jsonl`。当前包含节点、信道和网卡的故障/恢复、数据流负载的
增加/降低以及新增数据流，共九类事件。

生成优化场景 Twin：

```bash
python main.py twin opt
```

该命令处理 `opt/input` 中已经存在的优化场景；当前优化场景构造逻辑尚未实现。

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
  --scene-root generated_scenes
```

分析问题允许使用部分完成的 origin Twin；命令会报告已使用和跳过的场景数。只有没有
任何完整场景时才会要求先运行 `python main.py twin origin`。演化问题要求先完成
`python main.py twin evo`，通过比较变更前后的 Twin 生成实际满足目标变化的问题。

问题生成不会删除或覆盖已有问题列表和导出模板。目标问题输出已经存在时，命令会立即
停止并提示先运行：

```bash
python main.py clean questions
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
- `seed`：可复现划分所使用的随机种子。

`-r` 是 `--train-ratio` 的简写，必须在 `0` 和 `1` 之间；配置文件不提供默认划分比例。

`train_output_root` 和 `test_output_root` 必须都不存在或为空。任一输出目录非空时，
`split` 会在创建临时数据前直接停止，不会删除或覆盖已有数据集；需要先显式清空对应目录
或在配置中改用新的输出路径。

划分以问题为单位，并在每个任务内按 `template_id` 分层处理，不按场景整体划分。同一
场景可以因为不同问题同时出现在训练集和测试集中。输出按
`analysis/evolution/optimization` 分任务，每个任务目录包含该任务的问题列表和
`scenes/`；其中 `scenes/` 直接保存问题涉及的 `<scene_id>.jsonl` Twin，不创建场景子目录，
也不复制原始输入、标签或其他文件。某个训练或测试分片中没有对应任务的问题时，不创建
该任务目录。

默认配置输出到：

```text
/home/STN-Runtime/datasets/scene_tasks/
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

清理全部生成内容：

```bash
python main.py clean
```

该命令使用默认的 `configs/example.yaml`，清空其 `output_root` 中的场景输入、Twin、
标签、演化派生输入和问题输出。使用其他场景配置时：

```bash
python main.py clean -c configs/example.yaml
```

只清理 Twin 层及其下游问题：

```bash
python main.py clean twin
```

该命令保留 `origin/input` 和 `opt/input` 中的原始场景输入，删除 origin/opt Twin 和
标签、全部 `evo/input` 与 `evo/scenes`，并删除所有问题列表和导出模板。使用其他问题
配置时可增加 `-c <question_config>`。

只清理问题：

```bash
python main.py clean questions
```

该命令只删除分析、演化和优化问题列表、导出模板以及兼容旧目录时发现的局部问题文件，
不删除场景输入、Twin 或标签。

所有生成命令都不会自动调用这些清理操作；清理只能由上述显式命令触发。

查看帮助：

```bash
python main.py --help
python main.py twin --help
python main.py questions --help
python main.py split --help
python main.py clean --help
```

## 环境准备

安装 Python 依赖：

```bash
cd /home/SceneBuilder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

首次使用 ns-3 时进行配置和编译：

```bash
cd /home/SceneBuilder/ns-3.44
./ns3 configure -d debug --enable-examples --disable-tests
./ns3 build TwinGenerate
```

之后返回项目根目录运行 SceneBuilder：

```bash
cd /home/SceneBuilder
```

## 输出结构

```text
generated_scenes/
├── origin/
│   ├── question_template.yaml
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
│   ├── question_template.yaml
│   ├── evolution_questions.jsonl
│   ├── input/
│   │   └── <evolved_scene_id>/
│   │       └── 与原场景相同的输入文件及 labels.jsonl
│   └── scenes/
│       └── <evolved_scene_id>.jsonl
└── opt/
    ├── question_template.yaml
    ├── optimization_questions.jsonl
    ├── input/
    │   └── <optimization_scene_id>/
    │       └── 优化场景输入及 labels.jsonl
    └── scenes/
        └── <optimization_scene_id>.jsonl
```

普通场景生成始终写入 `origin/input`，`twin origin` 将 Twin 写入 `origin/scenes`。
`twin evo` 基于原场景创建新输入并分别写入 `evo/input` 和 `evo/scenes`。三个
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

数据流状态的判断优先级为 `failed > unstable > degraded > normal`：无统计、未发送或未接收数据时为 `failed`；成功接收但有丢包时为 `unstable`；无丢包但吞吐量低于需求带宽的 95% 时为 `degraded`；其余情况为 `normal`。

问题文件的位置由 `configs/question_generator.yaml` 中各类别的 `output_file` 决定。
默认分析问题写入 `generated_scenes/origin/analysis_questions.jsonl`。

演化问题的总列表默认写入
`generated_scenes/evo/evolution_questions.jsonl`。每条记录除通用字段外，还包含：

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
- `questions_per_question`：每条问题模板期望生成的总数量。
- `template_file`：该类问题使用的模板文件。
- `output_file`：生成问题的 JSONL 输出位置。
- `enabled`：是否启用对应的问题类别。

每次成功生成某一类问题时，生成器会把该类 `template_file` 原样复制到问题文件同目录的
`question_template.yaml`。因此 `origin`、`evo` 和 `opt` 都包含问题列表、原始模板、
`input/` 和 `scenes/`，下游可以直接从模板的 `answer` 字段读取输出约束。

模板文件是 `schema_version: 1` 的 YAML。所有任务的 `templates` 使用相同结构，每项只
包含唯一的 `id`、问题文本 `question` 和答案契约 `answer`。分析、演化和优化模板 ID
分别使用 `TA`、`TE`、`TO` 前缀，后接四位数字；问题生成器根据模板 ID 选择对应生成规则。
演化模板文件的 `events` 与 `templates` 相互独立：`events` 供 `twin evo` 构造演化场景
和 Twin，`templates` 供 `questions -t evolution` 根据前后 Twin 的证据生成具体问题。
每个事件条目包含 `id`、`entity_type`、`change` 和非空的 `description`；描述用于说明该
事件对场景的具体修改语义。

演化类的 `options.scenes_per_event` 使用一个正整数控制每类事件生成多少个场景；例如设置
为 `3` 时，九类事件各生成 3 个场景，最多生成 27 个演化场景。该区域还可配置流量增减
倍率、新增流的 `flow_addition_demand_mbps_range` 需求带宽范围，以及优先选择事件相关
目标的概率。同一事件类型内部从符合条件的原场景中随机不放回抽取；不同事件类型彼此
独立，可以复用同一个原场景，前面的事件不会消耗后续事件的候选场景。
演化问题比较吞吐量、时延和信道承载带宽等连续数值时，变化绝对值不超过原值的 `1%`
判定为 `unchanged`；`lost_packets` 属于整数计数，只有数值完全相等才判定为
`unchanged`。
当前已经实现分析类与演化类问题生成；优化类保留入口，但尚未启用完整生成逻辑。

## 常用选项

- `--stop-time <秒>`：以绝对仿真时刻覆盖默认停止时刻；必须晚于应用启动时刻。
- `--progress-interval <秒>`：设置 ns-3 仿真进度报告间隔，`0` 表示关闭。
- `--no-build`：跳过运行前的显式编译步骤。
- `--continue-on-error`：单个场景失败后继续处理其他场景。
- `--dry-run`：只打印将执行的 ns-3 命令。`twin evo` 会创建派生场景，因此不支持该选项。
- `questions --scene-root <路径>`：覆盖问题配置中的孪生体场景目录。

`scaleFactor` 会按相同比例缩小仿真中的信道容量、流量需求和队列包数。Twin 中的速率会
恢复到原网络口径，`queue_size_packets` 也仍表示原网络的队列容量；队列当前包数按队列
占用比例恢复。队列至少保留一个仿真包。

`twin evo` 不支持 `--stop-time`。变更前后 Twin 必须使用相同的场景仿真时长，才能直接比较
吞吐量、丢包数和平均时延。

## 当前约定

- 运行时事件功能当前处于禁用状态。场景表示一个固定网络状态，ns-3 不会在仿真途中注入事件。
- `twin evo` 构造变更前和变更后的独立场景；演化问题命令只比较已经生成的两个 Twin。
- 每个场景生成一个 `scenes/<scene_id>.jsonl` Twin 和一个
  `input/<scene_id>/labels.jsonl` 标签文件。
- 场景生成和 ns-3 仿真均串行执行，避免同时运行多个大规模仿真任务。

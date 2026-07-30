# SceneBuilder

SceneBuilder 用于批量构建网络实验数据。它将拓扑与随机配置转换为可供 ns-3 读取的网络场景，运行内置的 ns-3.44 仿真生成数字孪生体，并可进一步从孪生体生成带标签的问题数据。

整个流程分为三个阶段：

1. **场景生成**：生成节点、信道、网卡、路由和流量等静态输入。
2. **Twin 生成**：按 `origin`、`evo` 或 `opt` 类型运行 ns-3。
3. **问题生成**：只读取已经完成的 Twin 和标签，生成具体问题。

所有步骤统一通过项目根目录的 `main.py` 执行。

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

## 使用方法

命令格式为：

```bash
python main.py <模式> [选项]
```

必须明确指定以下一种模式：

- `generate`：生成网络场景。
- `twin`：生成指定类型的 Twin。
- `questions`：从已有 Twin 和标签生成问题。
- `clean`：清理场景配置对应的已有场景目录。

不指定模式或输入错误模式时，程序会列出全部可用模式并提示查看帮助。

### 1. 生成场景

```bash
python main.py generate -c configs/example.yaml
```

场景会生成到配置文件的 `output_root`，当前示例配置对应：

```text
/home/SceneBuilder/generated_scenes/origin/scenes
```

一次生成的场景数量为：

```text
符合 max_topology_nodes 限制的拓扑数量 x scenes_per_topology
```

注意：重新执行场景生成时，会完整清空配置指定的 `output_root`，包括此前生成的
`origin`、`evo`、`opt` 场景、Twin、问题文件和问题模板，然后只创建并生成新的
`origin/scenes`。请勿将 `output_root` 指向需要保留其他文件的目录。

### 2. 生成 Twin

原始场景 Twin：

```bash
python main.py twin origin
```

重新生成 origin Twin 会先删除全局及各场景内的分析问题输出、分析问题导出模板，清空
`evo/scenes`，并删除已有的演化问题输出及其导出模板。这些内容依赖旧的 origin Twin，
必须重新运行对应的问题生成命令；演化数据还必须先重新运行 `twin evo`。

演化场景及 Twin：

```bash
python main.py twin evo
```

该命令只从 `origin/scenes` 中同时具有 `twin.jsonl` 和 `labels.jsonl` 的场景里随机抽样；
缺少完整 Twin 输出的场景会被跳过，只有一个可用场景都没有时才会提示先运行
`python main.py twin origin`。它会读取 `question_generator/templates/evolution.yaml`
中独立定义的 `events` 目录，按每种事件从符合条件的原场景中随机抽样；原场景在本次
抽样中不重复使用。随后，命令将事件直接施加到复制出的静态场景上，并在 `evo/scenes`
中生成 Twin。当前目录包含节点、信道和网卡的故障/恢复、数据流负载的增加/降低，以及新增
数据流，共九类事件。新增流事件会选择一个静态路由可达且尚无现有流的源宿节点对，
分配新的流 ID，并按配置范围随机生成需求带宽。

优化场景 Twin：

```bash
python main.py twin opt
```

该命令处理 `opt` 中已经存在的优化场景；当前优化场景构造逻辑尚未实现。

### 3. 生成问题

```bash
python main.py questions -t analysis -c configs/question_generator.yaml
```

问题类型必须明确指定为以下一种：

- `analysis`：分析类问题。
- `evolution`：演化类问题。
- `optimization`：优化类问题。

运行时会显示本次生成的问题类型。`-c` 表示问题生成配置；不填写时默认使用 `configs/question_generator.yaml`。例如使用默认配置生成分析类问题：

```bash
python main.py questions -t analysis
```

也可以覆盖孪生体场景目录：

```bash
python main.py questions -t analysis \
  -c configs/question_generator.yaml \
  --scene-root generated_scenes
```

分析问题生成器按照模板逐项生成问题。对于枚举标签，会尽量在不同标签之间均分数量；如果现有场景无法满足某个标签，程序会保留已经生成的问题并报告缺少的数量。

问题模板使用 YAML。每一种具体问题都有独立且稳定的 `template_id`：分析模板使用
`TA0001`、`TA0002` 等编号，演化模板使用 `TE0001`、`TE0002` 等编号，优化模板预留
`TO` 前缀。`template_id` 表示问题类型；生成后的每一道具体问题仍使用独立的
`question_id`，格式为 `Q00000001`。因此，同一个 `template_id` 可以生成多个不同的
`question_id`。

分析问题只读取 `origin/scenes` 中同时具有 `twin.jsonl` 和 `labels.jsonl` 的完整场景。
允许在 `twin origin` 尚未处理完全部场景时生成问题；命令会报告已使用和跳过的场景数。
只有一个完整场景都没有时，才会提示先运行 `python main.py twin origin`。

生成演化问题时使用：

```bash
python main.py questions -t evolution -c configs/question_generator.yaml
```

该命令不会创建场景或运行 ns-3；它要求先运行 `python main.py twin evo`。生成器读取
`evo/scenes` 中的事件元数据和 Twin，比较对应的原场景 Twin，再按目标标签寻找实际满足变化的
实体。例如生成 `increase` 标签时，只有确实观测到对应指标升高的实体才会写入问题。
一个演化场景可以为不同模板提供多道问题。

### 4. 清理场景

```bash
python main.py clean -c configs/example.yaml
```

该命令只删除配置所对应 `output_root` 下可识别的场景目录，不删除其他普通文件或目录。

## 输出结构

```text
generated_scenes/
├── origin/
│   ├── question_template.yaml
│   ├── analysis_questions.jsonl
│   └── scenes/
│       └── <original_scene_id>/
│           ├── metadata.json
│           ├── nodes.csv
│           ├── channels.csv
│           ├── nics.csv
│           ├── routing_matrix.csv
│           ├── traffic.jsonl
│           ├── twin.jsonl
│           ├── labels.jsonl
│           └── analysis_questions.jsonl
├── evo/
│   ├── question_template.yaml
│   ├── evolution_questions.jsonl
│   └── scenes/
│       └── <evolved_scene_id>/
│           └── 与原场景相同的场景文件
└── opt/
    ├── question_template.yaml
    ├── optimization_questions.jsonl
    └── scenes/
        └── <optimization_scene_id>/
            └── 优化场景文件
```

普通场景生成始终写入 `origin/scenes`；`twin evo` 基于其中的原场景创建新场景，并只写入
`evo/scenes`。演化场景名末尾使用事件场景 ID，例如
`example_id2001_York_t2s_evo_E00000001`。三个目录中的数字场景 ID 共用同一编号空间。

场景输入文件的作用：

- `metadata.json`：场景来源、随机种子、生成规则和数量统计。
- `nodes.csv`：节点及其基础状态。
- `channels.csv`：节点之间的信道、原始容量和基础状态。
- `nics.csv`：独立网卡实体、所属节点、信道、队列配置和基础状态。
- `routing_matrix.csv`：物理故障发生前，按初始完整拓扑计算出的静态出口接口索引；初始拓扑不可达时为 `-1`。后续物理故障不会重算或改写该文件。
- `traffic.jsonl`：场景中的数据流及其需求和流量模型。

`twin.jsonl` 是 ns-3 输出的数字孪生体。每行表示一个实体，例如节点、网卡、信道或数据流，包含实体 ID、属性和关系，不包含标签。

数据流的 `demand_mbps` 和 `throughput_mbps` 都采用应用层有效载荷口径；
`throughput_mbps` 不包含 IPv4 和 UDP 头部。信道和网卡的带宽统计仍表示网络实际承载的
数据量，因此保留相应协议开销。

`labels.jsonl` 独立保存能够由分析问题生成器直接使用的答案。节点、网卡、信道和数据流
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
直接使用，不再从 `twin.jsonl` 重复推导或交叉校验。当前十一类分析标签与模板一一对应：

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

局部的 `evolution_questions.jsonl` 只写在对应的新场景中，可以包含该场景支持的多道演化
问题；原场景不保存这些问题。新场景的 `metadata.json` 会记录来源场景、事件场景 ID 和
静态变更内容。所有问题类型都只在至少生成一道问题时创建 JSONL；问题列表为空时不会保留
空文件。

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
`question_template.yaml`。因此 `origin`、`evo` 和 `opt` 都是包含问题列表、原始模板和
`scenes/` 的自包含数据集目录，下游可以直接从模板的 `answer` 字段读取输出约束。

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

查看全部命令参数：

```bash
python main.py --help
python main.py twin --help
python main.py questions --help
```

## 当前约定

- 运行时事件功能当前处于禁用状态。场景表示一个固定网络状态，ns-3 不会在仿真途中注入事件。
- `twin evo` 构造变更前和变更后的独立场景；演化问题命令只比较已经生成的两个 Twin。
- 每个场景当前生成一个基础孪生体文件 `twin.jsonl` 和一个标签文件 `labels.jsonl`。
- 场景生成和 ns-3 仿真均串行执行，避免同时运行多个大规模仿真任务。

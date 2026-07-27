# SceneBuilder

SceneBuilder 用于批量构建网络实验数据。它将拓扑与随机配置转换为可供 ns-3 读取的网络场景，运行内置的 ns-3.44 仿真生成数字孪生体，并可进一步从孪生体生成带标签的问题数据。

整个流程分为三个阶段：

1. **场景生成**：生成节点、信道、网卡、路由和流量等静态输入。
2. **Twin 生成**：按 `origin`、`evo` 或 `opt` 类型运行 ns-3。
3. **问题生成**：只读取已经完成的 Twin，根据证据生成问题和标签。

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
- `questions`：从已有 Twin 生成问题和标签。
- `clean`：清理场景配置对应的已有场景目录。

不指定模式或输入错误模式时，程序会列出全部可用模式并提示查看帮助。

### 1. 生成场景

```bash
python main.py generate -c configs/example.yaml
```

场景会生成到配置文件的 `output_root`，当前示例配置对应：

```text
/home/SceneBuilder/generated_scenes/origin
```

一次生成的场景数量为：

```text
符合 max_topology_nodes 限制的拓扑数量 x scenes_per_topology
```

注意：重新执行场景生成时，会先清理 `output_root` 下已有的场景目录，再生成新场景。

### 2. 生成 Twin

原始场景 Twin：

```bash
python main.py twin origin
```

演化场景及 Twin：

```bash
python main.py twin evo
```

该命令只从 `origin` 中同时具有 `twin.jsonl` 和 `labels.jsonl` 的场景里随机抽样；
缺少完整 Twin 输出的场景会被跳过，只有一个可用场景都没有时才会提示先运行
`python main.py twin origin`。它会读取 `question_generator/templates/evolution.yaml`
中独立定义的 `events` 目录，按每种事件从符合条件的原场景中随机抽样；原场景在本次
抽样中不重复使用。随后，命令将事件直接施加到复制出的静态场景上，并在 `evo` 中生成
Twin。当前目录包含节点、信道和网卡的故障/恢复、数据流负载的增加/降低，以及新增
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

分析问题只读取 `origin`。如果其中存在未生成 Twin 的场景，命令会停止并提示先运行
`python main.py twin origin`。

生成演化问题时使用：

```bash
python main.py questions -t evolution -c configs/question_generator.yaml
```

该命令不会创建场景或运行 ns-3；它要求先运行 `python main.py twin evo`。生成器读取
`evo` 中的事件元数据和 Twin，比较对应的原场景 Twin，再按目标标签寻找实际满足变化的
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
│   └── <original_scene_id>/
│       ├── metadata.json
│       ├── nodes.csv
│       ├── channels.csv
│       ├── nics.csv
│       ├── routing_matrix.csv
│       ├── traffic.jsonl
│       ├── twin.jsonl
│       ├── labels.jsonl
│       └── <question_type>_questions.jsonl
├── evo/
│   └── <evolved_scene_id>/
│       └── 与原场景相同的场景文件
└── opt/
    └── <optimization_scene_id>/
        └── 优化场景文件
```

普通场景生成始终写入 `origin`；`twin evo` 基于其中的原场景创建新场景，并只写入
`evo`。演化场景名末尾使用事件场景 ID，例如
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

`labels.jsonl` 独立保存标签。节点、网卡、信道和数据流状态分别写在 `node_state`、`nic_state`、`channel_state`、`data_flow_state` 行中，每行的 `label` 都是由 `{entity_id, label}` 构成的列表。节点状态为 `normal`、`disabled` 或 `routing_failed`；`network_state` 行保存全网状态，取值为 `normal`、`congested` 或 `faulty`。

`bottleneck` 行保存可确认的流瓶颈，格式为 `{data_flow_id, channel_id}`。仅当一条流的路径上恰好有一个 `saturated` 信道、其余信道全部为 `normal` 时才写入该标签。生成瓶颈链路问题时还要求该流的完整路径至少包含两条信道，单跳流的瓶颈标签不会被抽取为问题；列表为空或没有满足路径长度门槛的标签时，该场景不会生成瓶颈问题。

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

`data_flow_failure_cause` 行保存导致数据流 `failed` 的唯一物理故障实体，格式为 `{data_flow_id, entity_id}`，其中 `entity_id` 只能是节点 ID 或链路 ID。路径链路的网卡为 `disabled` 时，根因统一归并为所属链路 ID，不输出网卡 ID。`routing_failed` 是物理故障造成的派生节点状态，不作为独立根因；去重后恰好只有一个故障节点或链路时才写入。生成流失败根因问题时还要求目标流的完整路径至少包含两条信道，即路径上至少存在一个中间节点；单跳流、多故障或没有明确根因时不生成该问题。

分析问题的实体状态候选仅限数据流覆盖范围：节点必须出现在至少一条流的路径中，链路必须属于至少一条流的 `path_channels`，网卡必须属于这些路径链路。未被任何流经过的实体不会被抽取。数据流实体通过 `path_nodes` 和 `path_channels` 显式保存故障前静态路由确定的完整路径；物理故障不会使这两个字段缩短、清空或改为备用路径。

问题生成采用“私有标签 + 公开证据”双重门槛：`labels.jsonl` 只提供标准答案，不作为答题证据；生成器必须能仅根据 `twin.jsonl` 独立推导出相同且唯一的答案，否则跳过该场景中的候选。十一类分析问题的门槛如下：

| 问题 | 公开证据门槛 |
| --- | --- |
| 节点状态 | 节点位于流路径中，并且能由全网流结果与完整静态路径唯一定位一个物理故障。节点本身是唯一故障节点时判为 `disabled`；节点保留的任一静态路由仍将该故障节点作为下一跳，或仍从故障链路对应网卡转发时，判为 `routing_failed`。无上述故障证据但节点有收发包或至少一条相邻链路可用时可判为 `normal`；答案不唯一时不出题。 |
| 链路状态 | 链路位于流路径中，具有原始容量，并且 `carries` 与所有流的完整路径一致。按流路径将 `carries.bandwidth_mbps` 分为两个方向并分别求和，取较大方向：达到原始容量的 95% 时判为 `saturated`；处于原始容量的 70% 至 90%（含边界）时判为 `normal`，因为配置允许的退化倍率最高为 0.5，退化链路不可能达到该区间。其他情况下逐方向比较两端网卡：发送端至少有 10 个 `tx_packets` 时，以其 `tx_rate_mbps` 作为直接送入该链路的流量，以对端 `rx_rate_mbps` 作为该链路实际交付的流量，并令预期吞吐量为 `min(tx_rate_mbps, 原始容量)`；对端接收量为零时可判为 `disabled`，大于零但低于预期值 95% 时可判为 `degraded`。该证据适用于路径中的任意一跳，其余证据不足的情况不出题。 |
| 网卡状态 | 网卡属于流路径链路。若所属链路能由公开证据唯一判为 `disabled`，则该网卡在运行意义上同样不可用，判为 `disabled`；这不表示网卡自身一定是物理故障根因。链路任一方向当前吞吐量为正且队列字段完整时，按队列占用率判为 `normal` 或 `saturated`。网卡仅使用 `normal`、`disabled`、`saturated` 三种状态。 |
| 数据流状态 | `tx_packets`、`rx_packets`、`lost_packets`、`throughput_mbps` 和 `demand_mbps` 完整，并能按状态优先级唯一重算。 |
| 路径带宽约束 | 流的需求和完整路径、路径信道的原始容量与 `carries` 必须完整。先按流路径重建每条信道的两个方向总吞吐量，再从较大方向总吞吐量达到原始容量 95% 的路径信道中选择原始容量最小的瓶颈信道；其原始容量严格小于流需求时判为 `insufficient_channel_capacity`，否则判为 `traffic_congestion`。没有饱和瓶颈信道时不生成，信道是否退化不参与分类。 |
| 路径拥塞模式 | 每条路径链路都必须能由原始容量、`carries`、完整流路径和逐跳网卡收发证据确认状态；存在无法唯一判断的低负载链路时不生成。 |
| 信道饱和原因 | 根据信道 `carries` 和完整流路径重建的较大方向总吞吐量达到原始容量的 95%，并且所有流需求完整；最大流需求严格大于其余流之和时为 `single_large_flow`，否则为 `multiple_flow_aggregation`。 |
| 瓶颈链路 | 数据流的完整路径至少包含两条信道，每条路径链路状态都必须能从公开证据唯一判断且恰好只有一条 `saturated` 链路；单跳流或低吞吐链路存在状态歧义时不生成。 |
| 流失败根因 | 目标流的完整路径至少包含两条信道，即至少存在一个中间节点；所有流都具有完整静态路径和可重算的公开状态。将每个零吞吐链路或无流量节点作为候选物理故障，比较“静态路径经过该实体的流集合”与“实际失败流集合”；只有全网恰好一个候选完全解释失败集合，且目标流路径经过该候选时才出题。故障节点至少需要两条相邻链路，避免叶节点崩溃与其唯一链路故障无法区分。 |
| 信道不可用来源 | 目标信道必须位于流路径中并能由公开证据判为 `disabled`。根据完整静态路径和全网流结果必须唯一定位一个物理故障；唯一故障是任一端点节点时为 `connected_node_fault`，唯一故障是目标信道时为 `channel_or_interface_fault`。网卡物理故障按所属信道归并；节点侧与信道侧仍有歧义时不出题。 |
| 网卡不可用来源 | 目标网卡必须属于流路径信道，其所属信道能由公开证据判为 `disabled`，并且全网流结果只能定位一个物理故障。唯一故障是该信道任一端点节点时为 `connected_node_fault`，唯一故障是所属信道时为 `channel_or_interface_fault`；任一端网卡的物理故障都归并为所属信道。答案不唯一时不出题。 |

任何公开字段缺失、路径不完整、存在多个可能答案，或公开推导结果与私有标签不一致，都会使候选被拒绝。若因此达不到配置的题目数量，生成器保留已生成题目，并在命令行报告实际数量。

数据流状态的判断优先级为 `failed > unstable > degraded > normal`：无统计、未发送或未接收数据时为 `failed`；成功接收但有丢包时为 `unstable`；无丢包但吞吐量低于需求带宽的 95% 时为 `degraded`；其余情况为 `normal`。

全网状态的判断优先级为 `faulty > congested > normal`。存在节点崩溃、节点路由故障、网卡故障、信道故障或数据流失败时为 `faulty`；没有故障，但至少一个链路或网卡为 `saturated` 时为 `congested`；其余情况为 `normal`。数据流的 `degraded` 或 `unstable` 状态本身不会把全网标记为拥塞。

问题文件的位置由 `configs/question_generator.yaml` 中各类别的 `output_file` 决定。默认分析问题输出到项目根目录的 `analysis_questions.jsonl`。

演化问题的总列表默认写入 `evolution_questions.jsonl`。每条记录除通用字段外，还包含：

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
- `scene_duration`：场景和默认仿真时长。
- `topology_sources`：Topology Zoo 或 BRITE 拓扑来源。
- `fault_generation`：全网正常、单故障和双故障的抽样概率，以及链路、网卡物理故障的状态分布。被抽中的节点物理故障固定为崩溃 `disabled`，不再随机生成独立路由故障。
- `link_generation`、`nics`、`routing`：信道、网卡、队列和路由生成规则。
- `traffic_matrix`、`flow_feature`：流数量、需求大小和流量模型。

配置中的相对路径均相对于该 YAML 文件所在目录解析。

路由表先在无故障的初始完整拓扑上按加权最短路径生成，随后才抽取和施加节点、链路或网卡物理故障。故障发生后不重算路由，也不自动切换备用路径；因此静态路径可能继续指向已经停用的节点、链路或网卡。

生成器不再主动抽取 `routing_failed` 故障，也不会随机删除路由表项。一个未崩溃节点若仍有静态路由把已崩溃节点作为下一跳，或仍通过已经停用的链路/网卡转发，则该节点状态派生为 `routing_failed`。该状态描述路由与当前物理网络不一致，真正的故障根因仍是原始节点崩溃或链路故障；网卡故障在根因标签中归并为所属链路故障。

### 问题配置

问题配置示例为 `configs/question_generator.yaml`，主要控制：

- `scenes_root`：包含场景及孪生体的目录。
- `seed`：问题实体选择的随机种子。
- `questions_per_question`：每条问题模板期望生成的总数量。
- `template_file`：该类问题使用的模板文件。
- `output_file`：生成问题的 JSONL 输出位置。
- `enabled`：是否启用对应的问题类别。

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
目标的概率。
当前已经实现分析类与演化类问题生成；优化类保留入口，但尚未启用完整生成逻辑。

## 常用选项

- `--stop-time <秒>`：覆盖场景元数据中的默认仿真时长。
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

# SceneBuilder

SceneBuilder 用于批量构建网络实验数据。它将拓扑与随机配置转换为可供 ns-3 读取的网络场景，运行内置的 ns-3.44 仿真生成数字孪生体，并可进一步从孪生体生成带标签的问题数据。

整个流程分为三步：

1. **场景生成**：生成节点、信道、网卡、路由和流量等静态输入。
2. **孪生体生成**：逐场景运行 ns-3，计算吞吐、时延、丢包、队列和实体状态等运行结果。
3. **问题生成**：读取孪生体，根据问题模板生成问题、标签和对应场景 ID。

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
- `twins`：基于已有场景运行 ns-3 并生成孪生体。
- `questions`：基于已有孪生体生成问题和标签。
- `clean`：清理场景配置对应的已有场景目录。

不指定模式或输入错误模式时，程序会列出全部可用模式并提示查看帮助。

### 1. 生成场景

```bash
python main.py generate -c configs/example.yaml
```

场景会生成到配置文件的 `output_root`，当前示例配置对应：

```text
/home/SceneBuilder/generated_scenes
```

一次生成的场景数量为：

```text
符合 max_topology_nodes 限制的拓扑数量 x scenes_per_topology
```

注意：重新执行场景生成时，会先清理 `output_root` 下已有的场景目录，再生成新场景。

### 2. 生成孪生体

默认对 `generated_scenes` 下的全部场景运行 ns-3：

```bash
python main.py twins
```

只运行其中一个场景时，在命令末尾提供该场景相对于项目根目录或 `generated_scenes` 的相对路径：

```bash
python main.py twins generated_scenes/SCENE_DIRECTORY_NAME
```

也可以只填写场景目录名：

```bash
python main.py twins SCENE_DIRECTORY_NAME
```

指定路径必须位于 `generated_scenes` 内，不能使用绝对路径或通过 `..` 访问其他目录。`main.py` 会先编译一次 `TwinGenerate`，再按顺序运行全部场景或指定场景。每个场景的孪生体和标签分别保存在场景目录的 `twin.jsonl` 与 `labels.jsonl` 中，不再写入 `ns-3.44/result`。

已经完成编译时，可以跳过显式编译步骤：

```bash
python main.py twins --no-build
```

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

问题生成器按照模板逐项生成问题。对于枚举标签，会尽量在不同标签之间均分数量；如果现有场景无法满足某个标签，程序会保留已经生成的问题并报告缺少的数量。

### 4. 清理场景

```bash
python main.py clean -c configs/example.yaml
```

该命令只删除配置所对应 `output_root` 下可识别的场景目录，不删除其他普通文件或目录。

## 输出结构

```text
generated_scenes/
└── <scene_id>/
    ├── metadata.json
    ├── nodes.csv
    ├── channels.csv
    ├── nics.csv
    ├── routing_matrix.csv
    ├── traffic.jsonl
    ├── twin.jsonl
    └── labels.jsonl
```

场景输入文件的作用：

- `metadata.json`：场景来源、随机种子、生成规则和数量统计。
- `nodes.csv`：节点及其基础状态。
- `channels.csv`：节点之间的信道、原始容量和基础状态。
- `nics.csv`：独立网卡实体、所属节点、信道、队列配置和基础状态。
- `routing_matrix.csv`：故障发生后，节点对之间按实际可达邻接图计算出的出口接口索引；不可达为 `-1`。
- `traffic.jsonl`：场景中的数据流及其需求和流量模型。

`twin.jsonl` 是 ns-3 输出的数字孪生体。每行表示一个实体，例如节点、网卡、信道或数据流，包含实体 ID、属性和关系，不包含标签。

`labels.jsonl` 独立保存标签。节点、网卡、信道和数据流状态分别写在 `node_state`、`nic_state`、`channel_state`、`data_flow_state` 行中，每行的 `label` 都是由 `{entity_id, label}` 构成的列表。节点状态为 `normal`、`disabled` 或 `routing_failed`；`network_state` 行保存全网状态，取值为 `normal`、`congested` 或 `faulty`。

`bottleneck` 行保存可确认的流瓶颈，格式为 `{data_flow_id, channel_id}`。仅当一条流的路径上恰好有一个 `saturated` 信道、其余信道全部为 `normal` 时才写入该标签。瓶颈问题只从此标签列表抽取；列表为空时，该场景不会生成瓶颈问题。

`data_flow_congestion_pattern` 行保存流路径的拥塞模式。路径链路全部为 `normal` 或 `saturated` 且恰好一条链路饱和时标记为 `single_channel_bottleneck`；至少两条链路饱和时标记为 `multi_channel_saturation`。路径无饱和链路，或包含 `disabled/degraded` 链路时不生成该标签。

`channel_saturation_cause` 行保存饱和信道的流量构成原因，格式为 `{channel_id, label}`。对经过该信道的全部数据流按 `demand_mbps` 求和；若最大流的需求严格大于其余流需求之和，标记为 `single_large_flow`，否则标记为 `multiple_flow_aggregation`。非饱和信道或没有可确认经过流的信道不生成该标签。

`data_flow_bandwidth_constraint` 行仍可保存仿真内部计算得到的流路径带宽约束，但 Twin
不公开有效容量，因此问题生成器不会据此生成需要从公开证据判断能力不足的问题。

链路实体只公开 `properties.original_capacity_mbps`、
`properties.current_throughput_mbps` 和 `properties.delay_ms`。前两者分别表示配置中的原始
容量和统计窗口内的当前实际吞吐量。Twin 不公开有效容量、可用带宽或利用率；问题生成器
会结合经过链路的数据流需求判断状态。

`data_flow_failure_cause` 行保存导致数据流 `failed` 的唯一故障实体，格式为 `{data_flow_id, entity_id}`，其中 `entity_id` 只能是节点 ID 或链路 ID。路径链路的网卡为 `disabled` 时，根因统一归并为所属链路 ID，不输出网卡 ID；流在某个 `routing_failed` 节点处因缺少到其目的节点的路由而中断时，根因记录为该节点 ID。去重后恰好只有一个故障节点或链路时才写入；多故障或没有明确根因时不生成该标签和对应问题。

`data_flow_failure_type` 行保存流的故障类型，格式为 `{data_flow_id, label}`。答案为节点崩溃 `node_crash`、链路故障 `channel_failure` 或路由故障 `routing_failure`。只有先唯一定位到故障实体，并能唯一确认该实体的公开状态时才生成标签和问题。

分析问题的实体状态候选仅限数据流覆盖范围：节点必须出现在至少一条流的路径中，链路必须属于至少一条流的 `path_channels`，网卡必须属于这些路径链路。未被任何流经过的实体不会被抽取。数据流实体通过 `path_nodes` 和 `path_channels` 显式保存实际路径。

问题生成采用“私有标签 + 公开证据”双重门槛：`labels.jsonl` 只提供标准答案，不作为答题证据；生成器必须能仅根据 `twin.jsonl` 独立推导出相同且唯一的答案，否则跳过该场景中的候选。十类分析问题的门槛如下：

| 问题 | 公开证据门槛 |
| --- | --- |
| 节点状态 | 节点位于流路径中；将公开 `routes` 与 twin 拓扑可达节点比较，拓扑可达但路由表缺项时判为 `routing_failed`；否则，有收发包或至少一条相邻链路可用时判为 `normal`，至少两条相邻链路且全部停用时才能在单故障假设下判为 `disabled`。 |
| 链路状态 | 链路位于流路径中并具有原始容量和当前吞吐量。当前吞吐量达到原始容量的 95% 时判为 `saturated`。否则只使用以该链路为第一跳、公开 `tx_packets` 合计不少于 10 的数据流作为直接发送证据，按方向汇总其需求并令预期吞吐量为 `min(发送需求, 原始容量)`；实际吞吐量为零时可判为 `disabled`，大于零但低于预期值 95% 时可判为 `degraded`。没有足够直接发送证据时不出题。低负载达到预期吞吐量仍不能排除未显现的能力下降，因此不据此生成 `normal` 状态题。 |
| 网卡状态 | 网卡属于流路径链路；链路当前吞吐量为正且队列字段完整时，按队列占用率判为 `normal` 或 `saturated`。当前吞吐量为零时无法仅凭公开证据确认链路及网卡是否停用，因此不生成对应候选。网卡仅使用 `normal`、`disabled`、`saturated` 三种私有状态。 |
| 数据流状态 | `tx_packets`、`rx_packets`、`lost_packets`、`throughput_mbps` 和 `demand_mbps` 完整，并能按状态优先级唯一重算。 |
| 路径带宽约束 | Twin 不公开有效容量，无法从公开证据判断 `insufficient_channel_capacity`，因此当前不生成此类问题。 |
| 路径拥塞模式 | 每条路径链路都必须能由原始容量、当前吞吐量和直接发送证据确认状态；存在无法唯一判断的低负载链路时不生成。 |
| 信道饱和原因 | 信道当前吞吐量达到原始容量的 95%，`carries` 与各流的完整路径一致且所有流需求完整；最大流需求严格大于其余流之和时为 `single_large_flow`，否则为 `multiple_flow_aggregation`。 |
| 瓶颈链路 | 每条路径链路状态都必须能从公开证据唯一判断且恰好只有一条 `saturated` 链路；低吞吐链路存在状态歧义时不生成。 |
| 流失败根因 | 流可由公开统计判为 `failed`，路径和全网链路状态完整；在配置的单故障假设下，公开故障现象只能对应一个路径节点或路径链路。流在缺少目的路由的 `routing_failed` 节点处中断时可定位到该节点；叶节点崩溃与其唯一相邻链路故障无法区分时不会出题。 |
| 流故障类型 | 必须先满足流失败根因的全部门槛并唯一定位实体；实体为停用链路时标记 `channel_failure`，节点公开状态为 `disabled` 时标记 `node_crash`，节点公开状态为 `routing_failed` 时标记 `routing_failure`。 |

任何公开字段缺失、路径不完整、存在多个可能答案，或公开推导结果与私有标签不一致，都会使候选被拒绝。若因此达不到配置的题目数量，生成器保留已生成题目，并在命令行报告实际数量。

数据流状态的判断优先级为 `failed > unstable > degraded > normal`：无统计、未发送或未接收数据时为 `failed`；成功接收但有丢包时为 `unstable`；无丢包但吞吐量低于需求带宽的 95% 时为 `degraded`；其余情况为 `normal`。

全网状态的判断优先级为 `faulty > congested > normal`。存在节点崩溃、节点路由故障、网卡故障、信道故障或数据流失败时为 `faulty`；没有故障，但至少一个链路或网卡为 `saturated` 时为 `congested`；其余情况为 `normal`。数据流的 `degraded` 或 `unstable` 状态本身不会把全网标记为拥塞。

问题文件的位置由 `configs/question_generator.yaml` 中各类别的 `output_file` 决定。默认分析问题输出到项目根目录的 `analysis_questions.jsonl`。

## 配置说明

### 场景配置

场景配置示例为 `configs/example.yaml`，主要控制：

- `output_root`：场景输出目录。
- `seed`：随机种子。
- `scenes_per_topology`：每个符合条件的拓扑生成多少个场景。
- `max_topology_nodes`：允许参与生成的最大拓扑节点数。
- `scene_duration`：场景和默认仿真时长。
- `topology_sources`：Topology Zoo 或 BRITE 拓扑来源。
- `fault_generation`：全网正常、单故障和双故障的抽样概率；`node_state_probabilities` 控制节点崩溃 `disabled` 与路由故障 `routing_failed` 的比例，默认各为 0.5。
- `link_generation`、`nics`、`routing`：信道、网卡、队列和路由生成规则。
- `traffic_matrix`、`flow_feature`：流数量、需求大小和流量模型。

配置中的相对路径均相对于该 YAML 文件所在目录解析。

路由表在故障状态确定后生成：`disabled` 节点、`disabled` 信道，以及任一端网卡为 `disabled` 的信道会先从可达邻接图中移除，再在剩余拓扑上计算加权最短路径；`degraded` 信道仍然可达。有备用路径时路由会绕行，没有备用路径时对应项为 `-1`。

节点被抽为 `routing_failed` 时，生成器会在上述真实物理路由计算完成后，从该节点当前可达的目的节点中随机选取非空子集，将对应 `routing_matrix.csv` 项改为 `-1`；当原本至少有两个可达目的节点时，会至少保留一个目的节点仍可达。该随机范围固定，不需要额外配置。节点网卡保持在线，ns-3 只会缺少这些目的地址的静态路由。

### 问题配置

问题配置示例为 `configs/question_generator.yaml`，主要控制：

- `scenes_root`：包含场景及孪生体的目录。
- `seed`：问题实体选择的随机种子。
- `questions_per_question`：每条问题模板期望生成的总数量。
- `template_file`：该类问题使用的模板文件。
- `output_file`：生成问题的 JSONL 输出位置。
- `enabled`：是否启用对应的问题类别。

当前已经实现分析类问题生成；演化类和优化类保留入口，但尚未启用完整生成逻辑。

## 常用选项

- `--stop-time <秒>`：覆盖场景元数据中的默认仿真时长。
- `--progress-interval <秒>`：设置 ns-3 仿真进度报告间隔，`0` 表示关闭。
- `--no-build`：跳过运行前的显式编译步骤。
- `--continue-on-error`：单个场景失败后继续处理其他场景。
- `--dry-run`：只打印将执行的 ns-3 命令，不启动仿真或创建孪生体目录。
- `twins [场景相对路径]`：省略路径时处理全部场景，提供路径时只处理一个场景。
- `questions --scene-root <路径>`：覆盖问题配置中的孪生体场景目录。

查看全部命令参数：

```bash
python main.py --help
python main.py twins --help
```

## 当前约定

- 运行时事件功能当前处于禁用状态。场景表示一个固定网络状态，ns-3 不会在仿真途中注入事件。
- 演化实验应构造变更前和变更后的两个独立场景，并比较两个孪生体。
- 每个场景当前生成一个基础孪生体文件 `twin.jsonl` 和一个标签文件 `labels.jsonl`。
- 场景生成和 ns-3 仿真均串行执行，避免同时运行多个大规模仿真任务。

## 测试

```bash
cd /home/SceneBuilder
PYTHONPATH=. pytest -q
```

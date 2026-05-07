# Network Scene Generator

## 简介

本项目是一个可复现的网络场景生成器，用来根据拓扑与配置批量生成网络实验场景。它会输出节点、链路、网卡、路由矩阵、流量与事件等结构化文件，但本身不是仿真器，不直接计算丢包率、时延、抖动、吞吐等运行态性能指标。

它的典型用途是作为上游场景生产工具，与 `ns-3` 等网络仿真器对接：先用本项目生成场景文件，再将这些文件转换或接入仿真器运行，从而获得性能数据。这样的工作流适合做数据集构建、协议评估、参数扫描、压力测试和批量自动化实验。

## 支持配置

- 场景级配置：`output_root`、`seed`、`num_scenes`、`scene_duration`
- 拓扑来源配置：支持 `brite` 和 `topologyzoo`，可配置来源权重、目录与匹配模式
- 节点配置：节点类型推断、可信角色字段、位置信息继承
- 链路配置：链路类型推断、带宽生成、是否保留输入带宽
- 网卡配置：队列类型模式、队列大小、IP 地址池、子网前缀与 MAC 生成
- 路由配置：链路随机权重范围，并基于加权最短路径生成路由矩阵
- 流量矩阵配置：`uniform`、`exponential`、`gravity`、`spike`
- 流量特征配置：`poisson`、`on_off`、`cbr`，支持 `mixed` 和 `single` 两种整体模式
- 事件配置：`node_failure`、`link_failure`、`route_switch` 及其时间与概率参数

这是一个“网络场景生成器”，不是仿真器。它只负责按配置随机生成场景目录，不计算任何运行态指标（如丢包率、时延、抖动、吞吐）。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
python -m network_scene_generator --config configs/example.yaml
```

也支持显式写成：

```bash
python -m network_scene_generator generate --config configs/example.yaml
```

## 清理输出

清除某个配置对应 `output_root` 下的所有已生成场景目录：

```bash
python -m network_scene_generator clean --config configs/example.yaml
```

`clean` 只会删除看起来像场景输出的目录，不会删除同目录下其它无关文件或文件夹。

## 批量样本

- 使用 `num_scenes` 控制一次运行生成多少个场景目录。
- `num_scenes: 1` 表示单样本（默认值）。
- 使用 `scene_duration`（单位秒）表示场景时间维度，默认 `300`。
- 场景目录名固定为：`<config_stem>_id<场景ID>_<topology_stem>_t<时长>s`。
- `id` 字段固定零填充，最少4位（如 `id0001`），可稳定区分 100+ 场景。

## 输出文件

当前场景目录输出以下文件：

- `metadata.json`
- `links.csv`
- `nodes.csv`
- `routing_matrix.csv`
- `nics.csv`
- `events.jsonl`
- `traffic.jsonl`

## 关键约定

- 内部计算统一使用节点ID（非负整数，0开始）。
- 除 `nodes.csv` 外，其它文件不出现原始节点名。
- `nodes.csv` 中保留拓扑文件里的原始节点名，仅用于映射展示，不参与索引和内部计算。
- 节点角色仅使用 `core` / `aggregation` / `edge`，默认按拓扑结构做“简单推断+适度随机”生成。
- 节点角色按连通分量生成；同一场景内角色固定不变。
- 若输入拓扑提供可信角色字段，可通过配置开启优先使用。
- 链路角色不独立随机，而是由两端节点角色映射（`backbone/uplink/access/lateral`）并用于带宽抽样。
- 仅对 `aggregation-aggregation` 与 `core-edge` 两类链路施加轻微概率扰动（可配置）。
- 队列类型支持两种整体模式：`mixed`（混合）或 `single`（单一类型），并可按概率随机选择本场景使用哪一种模式。
- 网卡队列大小在同一场景内按节点角色统一抽样：相同角色节点的所有网卡使用相同 `queue_size_packets`。
- 网卡 IP 按链路随机分配不重叠子网：每条链路使用一个独立子网，两端网卡地址都落在该链路子网内；基础地址池与子网前缀都可配置概率。
- 路由固定使用 `weighted_shortest_path`：先为每条链路随机生成权重，再按权重计算最短路径；不再提供其它路由生成模式。
- 流量矩阵模型在每个场景开始时按 `traffic_matrix.mode_probabilities` 随机选一种；默认随机池为 `uniform` / `exponential` / `gravity` / `spike`。
- 流量特征模型支持两种整体模式：`mixed`（混合）或 `single`（单一类型），并可按概率随机选择本场景使用哪一种模式。
- `nodes.csv` 额外输出节点位置信息：若拓扑中有经纬度则写入，无则留空。
- `routing_matrix.csv` 是纯整数矩阵，不写表头和行索引。
- `routing_matrix.csv` 中不可达写 `-1`。
- `routing_matrix.csv` 中 `src==dst` 时单元格写源节点ID本身。
- `events.jsonl` 与 `traffic.jsonl` 使用 JSON Lines：每行一个 JSON 对象。
- `events.jsonl` 与 `traffic.jsonl` 采用按需字段输出：生成阶段只写必要字段，写文件阶段不做二次剔除。
- 事件发生时间 `event_time` 总是在 `[0, scene_duration]` 内随机生成。
- `node_failure` / `link_failure` 可按配置概率附带 `end_time`；若生成 `end_time`，则满足 `event_time <= end_time <= scene_duration`。
- `route_switch` 没有 `end_time`。
- 流量会自动施加硬约束：不可达的 OD 需求会被置零；可达流量不会超过其实际路由路径的瓶颈链路带宽。
- 对 `on_off` / `cbr` 流量，`demand_mbps` 也不会超过该流特征自身的速率上限。
- 多条流共享链路后产生拥塞仍然允许；这不属于“不可能配置”。
- `route_switch` 事件表示把某个 `(src,dst)` 的 `next_hop` 从 `old_next_hop` 改为 `new_next_hop`，不是切换路由算法。

## 文件字段

### links.csv

- `link_id`
- `src`（节点ID）
- `dst`（节点ID）
- `link_type`（`backbone` / `uplink` / `access` / `lateral`）
- `bandwidth_mbps`

### metadata.json

- `scene_name`
- `scene_id`
- `scene_duration`
- `seed`
- `config`
- `topology`
- `generation`
- `summary`
- `output_files`

其中 `generation.traffic_matrix` 会明确记录：

- 当前场景实际选中的流量矩阵模型，如 `uniform` / `exponential` / `gravity` / `spike`
- 该模型对应的实际规则参数 `active_rule`

其中 `generation.nics` 与 `generation.flow_feature` 只记录当前场景实际选中的模式，以及该模式对应的 `active_rule`。
其中 `generation.events` 会记录事件生成概率、候选类型以及 `failure_end_probability`。

### nodes.csv

- `node_id`
- `original_node_name`（拓扑文件原始节点名）
- `node_type`
- `latitude`（来自拓扑，若无则空）
- `longitude`（来自拓扑，若无则空）

### routing_matrix.csv

- 不写表头
- 不写行索引
- 第 `i` 行第 `j` 列表示节点 `i -> j` 的下一跳节点ID

`routing` 配置当前只支持：

- `weight_range`

### nics.csv

每行表示一个链路端点网卡；一条链路会对应两行网卡记录。

- `nic_id`
- `node`（节点ID）
- `link_id`
- `ip`
- `mac`
- `queue_policy`
- `queue_size_packets`

`nics` 配置支持：

- `queue_policy_mode_probabilities`
- `queue_policy_candidates`
- `queue_policy_probabilities`
- `single_queue_policy_probabilities`
- `ip_cidr`
- `ip_cidr_candidates`
- `ip_cidr_probabilities`
- `link_subnet_prefix`
- `link_subnet_prefix_probabilities`

### events.jsonl

每行一个事件对象。基础字段总是存在：

- `event_id`
- `event_time`
- `event_type`

其余字段按 `event_type` 按需出现：

- `end_time`（仅 `node_failure` / `link_failure` 且本次事件抽到“有结束时间”时出现）
- `target_type`
- `target_1`
- `target_2`
- `src`
- `dst`
- `old_next_hop`
- `new_next_hop`

### traffic.jsonl

每行一个流量对象。基础字段总是存在：

- `flow_id`
- `src`（节点ID）
- `dst`（节点ID）
- `demand_mbps`
- `feature_model`

其余参数字段按 `feature_model` 按需出现：

- `param_lambda`
- `param_on_mean`
- `param_off_mean`
- `param_peak_rate_mbps`
- `param_rate_mbps`
- `param_extra_1`
- `param_extra_2`

`flow_feature` 配置支持：

- `selection_mode_probabilities`
- `mode_probabilities`
- `single_model_probabilities`

`traffic_matrix` 配置支持：

- `mode_probabilities`
- `uniform_range_mbps`
- `exponential_scale`
- `gravity`
- `spike`




# WiFi 第二版：正常场景扩展

本分支沿用 SceneBuilder 原来的场景生成、ns-3 仿真和问题生成流程，加入 WiFi 接入层，
没有删除原有有线功能。这里只处理正常网络，不包含 WiFi 故障、演化、优化、漫游或 Mesh。

## 场景和数据约定

- `network_mode: wired` 保留原有有线方式，默认格式仍为 `legacy_csv`。
- `network_mode: hybrid` 使用有线骨干与 AP/STA 接入。当前将 aggregation 作为 AP、edge
  作为 STA，每个 STA 只选择一个 AP；没有终端的 AP 也可以生成、导入和输出 Twin。
- 示例支持 802.11g/802.11n、信道 1/6/11、发射功率、路径损耗参数、AP/STA 位置、
  终端静止或匀速移动，以及 FIFO/RED/CoDel/FqCoDel 队列。
- `scene_format: unified_jsonl` 将位置/移动写入 `nodes.jsonl`，网卡与队列写入
  `nics.jsonl`，无线信道及 AP/STA 成员关系写入 `channels.jsonl`；路由在 `routes.jsonl`，
  业务在 `traffic.jsonl`。不再另外输出 WiFi BSS/association 实体。
- Twin 保留 node、nic、channel、data_flow 四种实体。有线与无线 NIC 的公共字段顺序一致，
  使用 `interface_type` 区分，channel 使用 `medium_type` 区分；channel ID 统一为 C 前缀。
- 输入输出不单列 `ssid`；ns-3 内部使用无线 channel ID 作为 SSID。
- ns-3 使用生成的静态路由表，不在终端移动时自动切换 AP 或重新计算路由。

## 从仓库运行示例

使用 Linux 或 WSL，在项目根目录执行。Python 依赖在 `requirements.txt`；ns-3.48
的官方源码包、编译工具和编译产物不提交 Git，按根目录 README 初始化即可。
初始化仅构建 `network-scene` 模块及其依赖；此流程调用 C++ 仿真程序，不要求 Python bindings。

```bash
python -m pip install -r requirements.txt
# 首次使用时，替换为自己下载的 ns-3.48 源码包路径：
python main.py initial /path/to/ns-allinone-3.48.tar.bz2

python main.py scenes -c configs/wifi_v2_normal_example.yaml
python main.py twin -t origin -c configs/wifi_v2_normal_example_questions.yaml
python main.py questions -t analysis -c configs/wifi_v2_normal_example_questions.yaml
```

若 ns-3 已初始化且自定义 C++ 代码有更新，先在 `ns-3/` 中执行 `./ns3 build TwinGenerate`；
纯场景配置变化不需要重新编译 C++。普通 `twin` 命令也会进行增量构建。

输入拓扑为 `examples/topologies/custom_20node.gml`（2 个 core、4 个 AP、14 个 STA）。
两个 WiFi YAML 使用相对于配置文件的路径，不依赖本机交付目录。

输出位于 `generated/wifi_v2_normal_example/`：

- `origin/input/<场景名>/`：场景文件及 `labels.jsonl`。
- `origin/scenes/<场景名>.jsonl`：Twin。
- `origin/analysis_questions.jsonl`：分析问题。

程序不会覆盖已有输出。再次运行前应选用新的输出目录，或确认旧结果无需保留后使用
项目的 `clean` 命令。不要把整个生成目录提交 Git。

## 更改规模和已知限制

20 节点只是示例。换规模时需要换输入拓扑，并调整 `max_topology_nodes`；该字段是筛选
上限，不会自动把 20 节点拓扑变成 50 节点。AP 数、面积、流量和地址空间也应匹配场景规模。
当前随机种子的派生包含拓扑路径，改变存放位置后不保证生成逐字节相同的随机样本。

当前建议使用“每个终端在输入拓扑中只连接一个 AP”的结构。**一个终端连接两个候选 AP 时，
接入选择和路由计算可能不一致，此问题尚未修复。** 自动推断角色的任意外部拓扑也未全部验证。
原有演化/优化场景处理仍使用 CSV，不支持直接对 WiFi 合并 JSONL 场景执行这些工作流。

## 回归测试

```bash
# 无需仿真：34 项 schema、队列、SSID、AP 空闲等测试。
python -B -m unittest discover -s tools -p 'test_*.py'

# 先编译 TwinGenerate，再在一个全新的输出目录运行 26 组实际仿真。
python -B tools/verify_ap_without_stations.py \
  --phase check --output .runtime/wifi_regression
```

实际仿真覆盖现有 20 节点示例、10/30/50 节点、有线 CSV/JSONL、一个或多个 AP 没有终端、
全部 AP 没有终端、两个无线标准、四种队列、多种种子、移动及旧 CSV 无线输入。
测试同时检查 Twin/标签、问题答案、队列和设备 SSID。修改前后的 9 组基线曾完成数据文件
逐字节对比；原始空闲 AP 报错例也已通过 `main.py` 三步流程。测试覆盖不代表任意拓扑均受支持。

仓库保留源码、配置、手工输入拓扑和测试；`.gitignore` 排除本机环境、官方 ns-3 源码、
编译目录、生成场景/Twin/标签、交付包和历史本机辅助文件，不会删除磁盘上的这些文件。

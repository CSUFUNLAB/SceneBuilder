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

指定路径必须位于 `generated_scenes` 内，不能使用绝对路径或通过 `..` 访问其他目录。`main.py` 会先编译一次 `TwinGenerate`，再按顺序运行全部场景或指定场景。每个场景的孪生体保存在自身的 `twin` 子目录中，不再写入 `ns-3.44/result`。

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
    └── twin/
        └── 0.jsonl
```

场景输入文件的作用：

- `metadata.json`：场景来源、随机种子、生成规则和数量统计。
- `nodes.csv`：节点及其基础状态。
- `channels.csv`：节点之间的信道、原始容量和基础状态。
- `nics.csv`：独立网卡实体、所属节点、信道、队列配置和基础状态。
- `routing_matrix.csv`：节点对之间使用的出口接口索引。
- `traffic.jsonl`：场景中的数据流及其需求和流量模型。

`twin/0.jsonl` 是 ns-3 输出的数字孪生体。每行表示一个实体，例如节点、网卡、信道或数据流，包含实体 ID、运行标签、属性和关系，可供 STN-Runtime 导入或供问题生成器读取。

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
- `fault_generation`：全网正常、单故障和双故障的抽样概率。
- `link_generation`、`nics`、`routing`：信道、网卡、队列和路由生成规则。
- `traffic_matrix`、`flow_feature`：流数量、需求大小和流量模型。

配置中的相对路径均相对于该 YAML 文件所在目录解析。

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
- 每个场景当前只生成一个基础孪生体文件 `twin/0.jsonl`。
- 场景生成和 ns-3 仿真均串行执行，避免同时运行多个大规模仿真任务。

## 测试

```bash
cd /home/SceneBuilder
PYTHONPATH=. pytest -q
```

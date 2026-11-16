# tomlone — 自研 TOML 1.0 解析器 + 差分模糊测试

从零实现的、无第三方依赖的 [TOML 1.0.0](https://toml.io/en/v1.0.0) 解析器，
配套一套语法感知的差分模糊测试系统：把自研解析器与 stdlib `tomllib`、
Rust 系 `rtoml`、C++ 系 `pytomlpp` 三个引擎放在一起比对，自动发现、
最小化并归类不一致。

> **引擎版本与 TOML 版本（重要）**：Python 3.15 的 stdlib `tomllib` 已实现
> **TOML 1.1**（接受 `\x`/`\e` 转义、多行内联表、内联表尾逗号等 1.0 不允许的
> 写法）。差分判定以 **rtoml + pytomlpp 两个严格 TOML 1.0 引擎的共识**为规范
> 基准；当出现 2-2 平票（1.1 与 1.0 的版本差异）时，oracle 显式标记为
> `accept-split-other` 而非臆断。tomlone 的目标是严格 1.0，因此与两个严格
> 引擎一致即视为正确。官方 conformance 语料固定在 toml-test **v1.0.0 tag**。

## 目录结构

```
src/tomlone/          自研解析器（纯 Python，无依赖）
  _lexer.py           词法分析：字符串解码、转义、多行串、控制符
  _values.py          标量文法：整数/浮点/四种日期时间的严格解析
  _parser.py          递归下降：表/内联表/数组表的完整语义状态机
  _datetimes.py       保留全精度的日期时间值类型
  _errors.py          TOMLDecodeError
src/fuzz/             差分模糊测试框架
  engines.py          四引擎适配 + 异常隔离
  canonical.py        跨引擎值规范化（NaN/-0.0/日期精度归一）
  oracle.py           多数共识裁决 + 分歧分类 + 稳定签名
  generator.py        语法感知的合法文档生成器
  mutators.py         四层 20 种变异策略（字节/词法/结构/语义）
  minimizer.py        分层 ddmin 最小化（行级 → 字符级）
  runner.py           主循环、去重聚类、统计、增量持久化
tools/conformance/    官方 toml-test v1.0.0 语料的 Python runner
tools/fuzz/           CLI：run_fuzz.py / report.py
tests/                pytest 单元测试（含所有 fuzz 发现的回归用例）
third_party/toml-test 官方 conformance 语料（v1.0.0 tag）
scripts/run_overnight.sh  过夜 campaign 入口
```

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[fuzz]' pytest

# 1) 官方 conformance（要求 281/281 全过）
.venv/bin/python -I tools/conformance/run_conformance.py

# 2) 单元测试
.venv/bin/python -I -m pytest tests/ -q

# 3) 小规模 fuzz 冒烟
.venv/bin/python -I tools/fuzz/run_fuzz.py -n 2000 --seed 7 --out out/demo

# 4) 人读报告
.venv/bin/python -I tools/fuzz/report.py out/demo -o out/demo/REPORT.md

# 5) 过夜 campaign
scripts/run_overnight.sh 2000000 20261117
```

## 解析器设计要点

- **lexer 对数字/布尔/日期保持"哑"**：非结构字符先产出 `BARE` token，
  由 parser 按位置分类。因为 TOML 里 `.` 既是键分隔符又是小数点、纯数字
  也能当 bare key，词法阶段无法独立判定，必须推迟到值/键位置。
- **表身份用三条路径集合跟踪**：`defined`（已显式定义）、`closed`
  （内联表，禁止再插入）、`implicit`（隐式中间表）。数组表用整数索引标记
  路径，区分同一 AOT 的不同元素，避免 `[[arr]]` 重复时路径撞车。
- **空格分隔日期时间在 lexer 合并**：规范要求日期与时间间恰好一个
  U+0020，在空白仍可见时合并，从根上杜绝"多空格绕过"。
- **日期时间保留任意小数秒精度**：stdlib 的 `datetime` 只有 6 位微秒，
  自研类型保留原始写法；与其他引擎比对时再归一化，精度差异单独记录。

## 差分 oracle 怎么判

每次输入跑四个引擎，按结果分类：

| verdict | 含义 |
|---|---|
| `consensus` | 全部接受且值相同，或全部拒绝 |
| `precision` | 值相同但亚微秒精度不同（仅信息性） |
| `accept-split` | 一部分接受、一部分拒绝（**配置绕过/误拒类**） |
| `value-split` | 都接受但解码值不同 |
| `crash` | 某引擎抛了非解析异常（panic/断言/IndexError） |

分歧按多数共识裁决，2-2 平票显式标记而非臆断。每个分歧用稳定签名
（哪些引擎接受/拒绝、值树分组）去重，同一根因只留一条。

### 分歧自动归类（triage framing）

- `tomlone-false-accept (bypass risk)`：**自研接受、参考实现拒绝** ——
  最高优先级，正是历史上"配置被绕过"的形态。
- `tomlone-false-reject`：自研拒绝、参考接受 —— 兼容性 bug。
- `tomlone-value-divergence`：都接受但自研解码值与所有参考不同。
- `third-party-value-divergence` / `accept-split-other`：第三方引擎差异，
  用于评估各实现的规范符合度。

## 变异策略（语法感知）

不是纯随机字节翻转，而是四层、锚点感知：

1. **字节/字符层**：删/插/翻/复制字符、相邻交换、空白扰动、行拼接
2. **词法层（锚点感知）**：引号/转义/`\u` 码点/括号/数字文法/控制符/注释
3. **结构层**：重复键、重定义表、表头↔数组表互换、内联表破坏、dotted key
4. **语义层**：CRLF/裸 CR、内联表不可变、AOT 规则

变异器会先定位有意义的锚点（引号、`[`、`=`、数字串）再下手，产出率远高于
均匀随机。每个策略的实际产出率（disagreements/1000 uses）写进 stats，
可据此调权重。

## 最小化算法：分层 ddmin

经典 delta debugging 分两层跑：先在**行/语句级**删减（保留人类可读 TOML），
再在**字符级**收缩问题行。每层都有回退保护——若某轮最小化后不再复现分歧，
丢弃该轮、保留上一个已知能复现的输入，绝不会返回"复现不了的最小样例"。

## 输出物

每个 campaign 目录：

- `stats.json`：吞吐、结果分布、分类计数、**每变异策略产出率**
- `findings.json`：去重后的全部分歧（签名、分类、引擎裁决、最小化元数据）
- `cases/<sig>.toml`：每个分歧的最小复现样例
- `REPORT.md`：自包含的人读报告（内联所有最小样例）

所有结果增量落盘，中途 kill 不丢已发现的问题。

## 已知的第三方引擎差异（非本解析器 bug）

引擎版本实测：Python 3.15 `tomllib` 实现 **TOML 1.1**；`rtoml` 0.14 部分 1.1
（尾逗号、多行内联表、`\x` 转义）；`pytomlpp` 1.1.0 保持**严格 1.0**。
tomlone 以 pytomlpp + 官方 v1.0.0 语料为规范基准。下列差异中 tomlone 始终
站在严格 1.0 一侧：

| 输入形态 | tomlone | pytomlpp(1.0) | rtoml | tomllib(1.1) | 规范裁定 |
|---|---|---|---|---|---|
| 超 i64 整数（任意进制） | 拒绝 | 拒绝 | 十进制宽松 | 接受 | 1.0 规定 i64，超界报错 |
| 有限浮点溢出 `1e309` | 拒绝 | 拒绝 | 拒绝 | 接受为 inf | IEEE 有限字面量超界应报错 |
| 多行串内 CRLF | 归一为 `\n` | 归一 | **保留 `\r`** | 归一 | 必须归一，rtoml 偏差 |
| 内联表尾逗号 `{a=1,}` | 拒绝 | 拒绝 | 接受(1.1) | 接受(1.1) | 1.0 禁止，1.1 才允许 |
| 内联表裸换行 | 拒绝 | 拒绝 | 接受(1.1) | 拒绝 | 1.0 禁止 |
| 缺秒时间 `HH:MM` | 拒绝 | 拒绝 | 接受 | 接受 | 1.0 要求 `HH:MM:SS` |
| 闰秒 `:60` | 拒绝 | 拒绝 | 拒绝 | 拒绝 | ABNF 文本允许但全生态拒绝 |
| `inf`/`nan` 关键字 | 接受 | 接受 | 接受 | 接受 | 合法 |

报告里这些版本差异自动归到 `accept-split-other` /
`third-party-value-divergence`；只有 tomlone 与严格基准 pytomlpp 不一致时才
判为 `tomlone-*` 缺陷，不会被 1.1 引擎的宽松行为掩盖。


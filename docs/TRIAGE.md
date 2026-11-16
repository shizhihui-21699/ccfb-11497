# Fuzz Findings 处置指南

每个 campaign 产出 `findings.json`（去重后的分歧）+ `cases/*.toml`（最小复现）。
本指南说明如何分流，以及本仓库已确认的各类结论。

## 判定优先级

> **基准引擎（实测版本，非假设）**：Python 3.15 `tomllib` 实现 TOML **1.1**；
> `rtoml` 0.14 部分 1.1；只有 `pytomlpp` 1.1.0 保持严格 **1.0**。因此 tomlone
> 的对错以 **pytomlpp + 官方 toml-test v1.0.0 语料**为准。runner 的 `_category`
> 已按此判定：tomlone 与 pytomlpp 不一致才算 `tomlone-*` 缺陷，与 1.1 引擎的
> 差异归为版本分裂（`accept-split-other`）。

1. **`tomlone-false-accept (bypass risk)`** — 最高优先级。自研接受了严格 1.0
   基准拒绝的输入，正是历史上"配置被绕过"的形态。必须修。
2. **`tomlone-false-reject`** — 自研拒绝了基准接受的合法输入，兼容性 bug。
3. **`tomlone-value-divergence`** — 都接受但解码值与基准不同。
4. **`crash`** — 非解析异常（IndexError/AssertionError 等），健壮性缺陷。
5. **`accept-split-other` / `third-party-value-divergence`** — 引擎间（多为
   1.0/1.1 版本）差异，tomlone 已对齐严格 1.0，不阻塞。

## 标准处置流程

```bash
# 1. 复现：用最小样例跑四个引擎
.venv/bin/python -I -c "
import tomllib, rtoml, pytomlpp, sys; sys.path.insert(0,'src'); import tomlone
doc = open('CASES/xxx.toml').read()
for n,m in [('tomlone',tomlone),('tomllib',tomllib),('rtoml',rtoml),('pytomlpp',pytomlpp)]:
    try: print(n, m.loads(doc))
    except Exception as e: print(n, 'REJECT', type(e).__name__)
"

# 2. 查规范：官方语料有无对应 valid/invalid 用例
grep -rn "特征串" third_party/toml-test/tests/

# 3. 修复后：在 tests/test_parser.py 加回归测试（用最小样例）
# 4. 全量回归
make test && make conformance
# 5. 用相同 seed 重跑确认签名消失
```

## 已确认结论（截至当前版本）

经过累计 ~350 万次 fuzz，tomlone 自身的 accept/reject/value 分歧已清零。
所有剩余分歧均为第三方引擎差异，详见 README 的"已知第三方引擎差异"表。
关键裁定：

- **闰秒 `:60`**：TOML ABNF 文本允许 00-60，但 tomllib/rtoml/pytomlpp 全拒绝
  （无 datetime 类型可表示）。tomlone 对齐生态拒绝，保证值可往返。
- **超 i64 整数 / 浮点溢出**：Python 任意精度与 IEEE-754 inf 行为天然与
  tomllib 一致；C++/Rust 引擎选择报错，属语言模型差异而非缺陷。
- **rtoml 的 CRLF**：多行串内不把 `\r\n` 归一为 `\n`，违反 TOML 1.0 明确规定。

## 最小化质量保障

`minimizer.py` 的分层 ddmin 带**回退保护**：任何一轮最小化若导致输入不再复现
同一签名的分歧，该轮被丢弃、保留上一个已知有效输入。因此 `cases/` 里的样例
保证可复现，不会出现"缩到最后反而不触发"的情况。

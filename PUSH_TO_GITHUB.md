# GitHub 仓库

**已经推上去了**，这里是后续维护说明。

| | |
|---|---|
| 仓库 | https://github.com/cyberironman/WeChatMulti |
| 当前状态 | **公开（Public）** |
| 默认分支 | `main` |
| 下载页 | https://github.com/cyberironman/WeChatMulti/releases/latest |
| 认证方式 | Git Credential Manager（已登录 `cyberironman`，以后不用再输密码） |

> **仓库里不含任何腾讯的文件。** 微信客户端由使用者自己从腾讯官方渠道下载，
> 工具在安装时自动读取本机已装版本，所以仓库体积只有几十 MB，
> 而不是把官方目录镜像进来的 921 MB。

---

## 以后改了代码怎么推

```bat
cd /d "<本仓库目录>"
git add -A
git commit -m "v0.1.1：说明这次改了什么"
git push
```

凭据存在 Windows 凭据管理器里，不会再问密码。要换账号：

```bat
git-credential-manager github logout cyberironman
git-credential-manager github login
```

commit message 用**中文 + 版本号前缀**（`v0.1.1：…`），跟仓库风格保持一致。

---

## 发新版本（Release）

普通用户拿到的就是这个包，**不是仓库源码**。一条命令出包：

```bat
python scripts\build_release.py
```

产物在 `dist\`：

| 产物 | 说明 |
|---|---|
| `WeChatMulti-v<版本>-win64.zip` | 绿色版：解压即用，可放 U 盘，数据跟着文件夹走 |
| `WeChatMulti-v<版本>-win64.zip.sha256` | 校验和 |

然后：

**方式 A：网页**
仓库页右侧 **Releases → Draft a new release** →
Tag 填 `v0.1.1`（新建）→ 标题 → 把 zip 拖进附件框 → Publish release。

**方式 B：命令行（有 gh 时）**

```bat
gh release create v0.1.1 "dist\WeChatMulti-v0.1.1-win64.zip" --title "v0.1.1"
```

发完记得同步改版本号：`wechatmulti/__init__.py` 的 `__version__`、
`README.md` 首屏、`CHANGELOG.md`。

---

## 推送之前一定检查

```bat
:: 运行数据绝不能进仓库
git ls-files | findstr /I "instances.json guide_state.json .log .lnk"

:: 期望：没有任何输出
```

`.gitignore` 里 `/data/`、`/logs/`、`/Data/` 都挡着。
注意这些必须带开头的斜杠，否则会把源码目录也一起忽略掉。

---

## ★ 实验脚本的安全红线

**这一条比什么都重要。**

2026-10-30 出了一次事故：某个对照实验脚本为了「清场」写了批量 kill 微信进程的逻辑，
结果把用户正在使用的**微信 A / B / C 三个实例全部杀掉了**（数据没丢，重启即恢复，但不该发生）。

从那以后，项目里加了硬约束：

1. **所有实验只在 `_sandbox_*` 目录里跑** —— 路径必须带这个标记。
2. **`wechatmulti/sandbox.py` 里的 `assert_sandbox()` 会拒绝任何非沙箱路径**，
   包括 `D:\WeChatMulti\B`、`C:\Program Files\Tencent\Weixin`、`E:\WEIXIN` 等真实位置。
3. **sandbox 模块里没有任何 kill 函数的对外接口** —— 只读，不杀。
4. 验证「实例能不能起来」的判据必须是
   **「有没有带沙箱 profile 的新进程」**，绝不能是「别人的进程还在不在」。

新写实验脚本时，**先 import sandbox，不要自己写清场逻辑**。

---

## 常见问题

**Q：commit 作者显示不对？**

本仓库单独的 `user.name` / `user.email` 已经设好：

```bat
git config user.name
git config user.email
```

**Q：OneDrive 同步会不会把 `.git` 弄坏？**

正常使用没问题。如果两台机器同时改同一分支会冲突。
建议只在一台机器上提交，另一台只 `git pull`。

**Q：改了代码怎么确认打包出来的是新代码？**

看 exe 的**时间戳**，不能只看源码。

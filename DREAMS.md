# DREAMS — target list (upper bound; constitution is lower bound)

> Scope: uncompleted targets only. Delete on completion; history lives in `ledger/events.jsonl`.

## Rules

1. Read this file before starting work. Every task must map to one listed dream. Unmappable task → stop, ask user: add dream or drop task.
2. No invented work. Empty dream list → no code work.
3. On completion: delete entry + ledger `verification` (or `decision`) referencing original goal. Criteria = entry's 完成标准.
4. New dream: append entry + ledger `append --kind goal` (record returned E-xxxx in entry).
5. Conflict with ledger → ledger wins; fix this file.

## 非目标

- 替代 Cherry Studio（old-chain E-0073 voided; still void）

## 梦

- [ ] 像思维导图一样，沿着分支想透一个难题（用户，2026-09-28）
  完成标准（逐项打勾，全程真实数据，无示例凑数）：
  1. 第一屏 = 简洁聊天框，无示例；默认模块 = 聊天
  2. 发送后与 ChatGPT 一致（右气泡 + 流式回答）；首问即树根，树相暂不露
  3. 选中文字弹出：复制 / 划线 / 记笔记 / 分支
  4. 点分支 → 新空聊天框，初名"未命名"；首问后 AI 总结命名；可手动重命名
  5. 右键菜单三项：重命名 / 关闭 / 关闭其他标签页
  6. 返回上一级；原文荧光笔标色随机，同对话不重复；导图/树杈色同步
  7. 分支继承父上下文；层数无上限
  8. 树常驻导航，点击跳转；标签页浏览器手感（中键新开）；只借习惯不做浏览器
  9. 标签页显示分支名
  10. 划线 = 下划线，自选色，默认 8 色；分支随机池内置 40 色
  11. 记笔记秒进笔记本；正文夹两横线；flomo 卡片流倒序；有时、有出处（分支名），点出处跳回；"随便看看"出 random 旧卡
  12. 主侧边栏图标切换聊天 / 笔记 / 树；默认聊天
  13. 树模块导图 + 大纲，顶部一键切，默认导图
  账本：E-0002

```
- [ ] <one-line target>（proposer, date）
  完成标准：<decidable, no degree words>
  账本：E-xxxx
```

# 冻结前整树开发方案

`developmentPlan` 是每个节点存入 SQLite baseline 的机器契约，不是开发完成后的总结。宿主先结合只读代码检索起草完整层级 definition，再执行 `prepare-hierarchy`：每个节点目录生成自己的 `development-plan.md`，根级文件还会聚合整棵树。人工查看根级真实文件、选择开发方式并同意后，Agent 使用准备结果中的层级指纹和所选方式执行一次 `freeze-hierarchy`。

## 完整 definition 外壳

控制器使用严格字段集合，缺字段和未知字段都会拒绝。最外层固定为 `{"schemaVersion":3,"root":{"definition":{...},"children":[...]}}`；每个 child 继续使用相同的 `definition + children` 节点结构。三个层级的节点 definition 都必须先具备以下共同字段，再加入本层专有字段：

```json
{
  "schemaVersion": 3,
  "id": "t-protein-preparation-validation",
  "kind": "TASK",
  "gateLevel": "LIGHT",
  "title": "蛋白制备提交字段可选性",
  "goal": "允许指定电泳字段为空并保持其他校验不变。",
  "scope": [
    "erp-protein-core/src/main/java/com/majorbio/service/erp/protein/core/service/preparation/impl/PreparationTaskSubmitServiceImpl.java",
    "erp-protein-core/src/test/java/com/majorbio/service/erp/protein/core/service/preparation/PreparationTaskSubmitServiceTest.java"
  ],
  "nonGoals": ["不修改其他制备字段的必填规则。"],
  "requirements": [
    {"id": "R-001", "text": "两个指定电泳字段允许为空。"}
  ],
  "acceptance": [
    {
      "id": "A-001",
      "requirementIds": ["R-001"],
      "expectedResult": "两个字段为空时提交成功，其他必填字段缺失时仍失败。"
    }
  ],
  "testCommands": [["mvn", "-pl", "erp-protein-core", "-Dtest=PreparationTaskSubmitServiceTest", "test"]],
  "requiredSkills": [
    {
      "name": "tdd-workflow",
      "stages": ["DEVELOPMENT", "GATE"],
      "purpose": "完整执行测试先行、最小实现、重构和复测，并在内部门禁逐项说明应用情况。"
    }
  ],
  "risks": ["数据库或下游契约可能仍要求非空。"],
  "decisions": ["只放宽指定字段，不删除统一校验流程。"],
  "developmentPlan": {},
  "parentId": null,
  "execution": {
    "dependsOn": [],
    "inputs": ["蛋白制备任务提交参数"],
    "outputs": ["兼容空电泳字段的提交结果"]
  }
}
```

上例的空 `developmentPlan` 仅表示插入位置，提交前必须替换为下文完整 Task 对象，不能原样提交。共同字段规则：

- `schemaVersion` 固定为 `3`；`kind` 只能为 `DELIVERY|CAPABILITY|TASK`；协调层 `gateLevel` 固定 `FULL`，Task 为 `LIGHT|FULL`；
- `id` 为安全小写 ID；`title/goal` 和所有说明字符串必须非空且不能含 TBD/TODO/FIXME 等占位符；
- `scope/nonGoals/requirements/acceptance/testCommands/risks/decisions` 都是非空数组；测试命令是 argv 数组，不是 shell 字符串；
- `requiredSkills` 必须存在但可以为空；每项只含 `name/stages/purpose`。名称保存 catalog 标识而不是 `/skill` 命令，阶段只允许 `DEVELOPMENT|GATE|FINAL_REVIEW`，其中 `FINAL_REVIEW` 只在需求根声明；根级声明自动约束后代，不能由子项覆盖或取消；
- requirement ID 使用 `R-001` 形式，acceptance ID 使用 `A-001` 形式；每个 requirement 必须至少有一个 `requirementIds` 只包含自身的独立 acceptance，不能只用一个跨需求 acceptance 同时覆盖多个 requirement；跨需求 acceptance 仅用于追加集成行为验收；
- 每个独立 acceptance 的 `expectedResult` 必须写成可单独观察和取证的通过条件，不能使用“功能正常”“按预期工作”或对 requirement 的笼统复述；有关键失败、边界或兼容条件时一并写入；
- `scope` 只能是精确相对路径或尾部 `/**` 前缀，不能进入 `.layered-delivery`；
- 下列“允许空数组”的字段除外：根/无依赖的 `dependsOn`、Task `interfaces`、Task `dataAndTransactions`、协调层 `sharedContracts`。其他在示例中出现的数组均应按机器校验提供非空内容。

三个 kind 的专有字段集合固定为：

- 根或嵌套 Task：共同字段 + `developmentPlan` + `parentId` + `execution {dependsOn, inputs, outputs}`；没有 `children/decomposition`。根 Task 的 `parentId=null` 且 `dependsOn=[]`。
- 根或嵌套 Capability：共同字段 + `developmentPlan` + `parentId` + `decomposition {status, dependsOn}` + Task `children`。根 Capability 的 `parentId=null` 且 Capability `dependsOn=[]`。
- Delivery：共同字段 + `developmentPlan` + `decomposition {status}` + Capability `children`；没有 `parentId`。

`decomposition.status` 只能是 `OPEN|SEALED`。每个协调层 child 的完整形状如下，五个字段缺一不可：

```json
{
  "id": "t-relax-electrophoresis-validation",
  "kind": "TASK",
  "title": "放宽电泳字段校验",
  "requirementIds": ["R-001"],
  "acceptanceIds": ["A-001"]
}
```

Delivery 的 child 使用相同五个字段，但 `kind` 必须为 `CAPABILITY`。Capability 的 `developmentPlan.childPlans` 必须覆盖其全部 Task children；Delivery 的 `childPlans` 必须覆盖其全部 Capability children，且 R/A 映射与 child 完全一致。全部父子节点必须在同一次 `prepare-hierarchy` 中完整物化、一起评审并一次冻结。

## 场景选择

Task 的 `scenarios[].kind` 从以下值选择，可组合多个：

- `API`：HTTP/RPC/Controller/Service 对外或内部调用契约；
- `DOMAIN`：领域规则、校验、状态转换；
- `DATA` / `MIGRATION`：数据模型、查询、持久化、迁移与回滚；
- `CONFIG` / `BUILD`：配置项、依赖、构建与运行参数；
- `UI`：页面、组件、交互、状态与可访问性；
- `INTEGRATION`：跨模块/服务调用与失败处理；
- `REFACTOR`：保持可观察行为不变的结构调整；
- `TEST` / `DOCS`：测试资产或文档；
- `SECURITY` / `PERFORMANCE`：权限、敏感数据、并发、资源和性能预算；
- `OTHER`：以上都不匹配，并在 description 中明确实际场景。

不存在接口改动时 `interfaces` 使用空数组；不存在数据/事务改动时 `dataAndTransactions` 使用空数组。人类投影会明确显示“不涉及”，不要为了填表虚构接口或数据库变化。

## Task developmentPlan

Task 方案必须精确到文件和目标契约。`fileChanges.path` 只能是 scope 内的精确相对路径，不能使用 `/**` 或其他 glob。规划时必须沿每个接口/方法契约检查实现、公开说明和对应测试的所有者文件，避免把同一验收项的 Javadoc、注解、映射或测试遗漏到验证阶段。若冻结后仍发现这种同契约文件遗漏，使用原 Task 的追加验证修正，不创建新的需求根。

```json
{
  "purpose": "允许蛋白制备任务提交时电泳上样量和体积为空，同时保持其他字段校验不变。",
  "scenarios": [
    {
      "kind": "API",
      "title": "放宽提交接口的两个可选字段",
      "description": "调整提交服务校验，只取消电泳上样量和体积的非空限制，不改变字段格式和其他必填规则。",
      "requirementIds": ["R-001"]
    }
  ],
  "fileChanges": [
    {
      "path": "erp-protein-core/src/main/java/com/majorbio/service/erp/protein/core/service/preparation/impl/PreparationTaskSubmitServiceImpl.java",
      "action": "MODIFY",
      "purpose": "删除两个字段的强制非空校验并保留已有格式校验。"
    },
    {
      "path": "erp-protein-core/src/test/java/com/majorbio/service/erp/protein/core/service/preparation/PreparationTaskSubmitServiceTest.java",
      "action": "MODIFY",
      "purpose": "覆盖两个字段为空、单独为空和均有值的提交场景。"
    }
  ],
  "interfaces": [
    {
      "name": "蛋白制备任务提交",
      "kind": "METHOD",
      "action": "MODIFY",
      "location": "PreparationTaskSubmitServiceImpl 提交校验流程",
      "currentContract": "电泳上样量和电泳上样体积为空时提交失败。",
      "targetContract": "两个字段可独立为空或同时为空；非空值仍执行原有格式校验，其他必填字段规则不变。",
      "requirementIds": ["R-001"]
    }
  ],
  "logic": [
    "定位两个字段进入统一必填校验集合的位置并移除其必填标记。",
    "保留非空值的类型、范围和下游映射逻辑，不扩大到其他制备字段。"
  ],
  "dataAndTransactions": [],
  "compatibility": [
    "已有传值客户端行为不变；数据库字段必须已经允许空值，否则评审不通过。"
  ],
  "testPlan": [
    {
      "acceptanceIds": ["A-001"],
      "approach": "运行提交服务定向单测，验证空值组合成功且其他必填字段缺失仍失败。",
      "commandIndexes": [0]
    }
  ],
  "reviewPoints": [
    "确认数据库和下游调用允许空值。",
    "确认只放宽指定两个字段，没有删除整个样品校验分支。"
  ]
}
```

`interfaces[].kind` 允许 `HTTP_ENDPOINT|RPC|FUNCTION|METHOD|CLASS|EVENT|SCHEMA|CONFIG|CLI|UI|FILE_FORMAT|OTHER`，`action` 允许 `ADD|MODIFY|REMOVE`。`currentContract` 与 `targetContract` 必须让评审者看出变更前后差异。

## Capability developmentPlan

Capability 方案说明 Task 如何组合成一项能力。`childPlans` 与 Capability 的全部 Task children 一一对应；`dependsOn` 冻结兄弟 Task 依赖。

```json
{
  "purpose": "把提交校验调整和回归测试组合成可独立验收的蛋白制备提交能力。",
  "childPlans": [
    {
      "id": "t-relax-electrophoresis-validation",
      "purpose": "调整提交服务的可选字段规则。",
      "deliverables": ["提交服务允许两个电泳字段为空。"],
      "requirementIds": ["R-001"],
      "acceptanceIds": ["A-001"],
      "dependsOn": []
    },
    {
      "id": "t-protein-preparation-regression",
      "purpose": "验证提交接口兼容性和其他必填规则。",
      "deliverables": ["空值组合与回归场景测试证据。"],
      "requirementIds": ["R-001"],
      "acceptanceIds": ["A-001"],
      "dependsOn": ["t-relax-electrophoresis-validation"]
    }
  ],
  "sharedContracts": [
    {
      "name": "蛋白制备提交字段可选性契约",
      "kind": "SCHEMA",
      "description": "实现 Task 提供新的字段可选性，回归 Task 按同一契约构造输入和断言。",
      "providerChildIds": ["t-relax-electrophoresis-validation"],
      "consumerChildIds": ["t-protein-preparation-regression"],
      "requirementIds": ["R-001"]
    }
  ],
  "integrationFlow": [
    "先实现并验证提交规则，再运行使用目标契约的回归测试，最后执行 Capability 聚合门禁。"
  ],
  "deliveryWaves": [
    {
      "order": 1,
      "name": "提交规则实现",
      "childIds": ["t-relax-electrophoresis-validation"],
      "exitCriteria": "实现 Task VERIFIED。"
    },
    {
      "order": 2,
      "name": "兼容性回归",
      "childIds": ["t-protein-preparation-regression"],
      "exitCriteria": "回归 Task VERIFIED 且聚合测试可运行。"
    }
  ],
  "testPlan": [
    {
      "acceptanceIds": ["A-001"],
      "approach": "运行 Capability 集成测试，验证实现与回归证据使用同一字段契约。",
      "commandIndexes": [0]
    }
  ],
  "reviewPoints": [
    "确认两个 Task 的边界独立且共享契约只有一个来源。"
  ]
}
```

## Delivery developmentPlan

Delivery 使用与 Capability 相同的字段结构，但 `childPlans` 对应 Capability，`sharedContracts` 表示跨 Capability 接口/交付契约，`deliveryWaves` 表示能力交付与顶层集成顺序。不要在 Delivery 中写业务代码文件；具体文件属于 Task 评审方案。

Delivery 下 Capability 的 `decomposition.dependsOn` 必须与 Delivery `childPlans[].dependsOn` 完全一致。Capability 下 Task 的 `execution.dependsOn` 同理必须与 Capability 计划一致。依赖有环、波次未覆盖全部子级或消费方不晚于提供方时，prepare 必须失败。

## 人工展示规则

`prepare-hierarchy --json` 成功后，宿主必须：

1. 提供返回的 `humanArtifacts.developmentPlan` 可点击入口；
2. 同时简述根 ID、完整树、开发目的、文件、接口、依赖波次、测试映射和 `nextAction`；
3. 明确这只是等待评审，尚未冻结，也没有开发授权；
4. 用户提出修改时重新准备同一根 ID 的完整树，不把旧文件当作已批准；
5. 用户选择 active/manual，并确认已评审同意当前文件，不要求知道或复述 SHA256；
6. Agent 用已保存的 `hierarchyFingerprint` 和所选方式调用一次冻结，成功后再次提供 plan、baseline 和 progress 入口。

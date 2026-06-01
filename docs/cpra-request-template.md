# California Public Records Act (CPRA) Request Template

> Paste the body below into the city's records portal. Replace every `<…>`
> placeholder. Keep the column list intact — it's what makes the response
> drop into our pipeline without manual cleanup.

---

## English (paste into the portal)

**Subject:** Public Records Act request — building permit data for `<CITY>`

Dear `<CITY>` Records Officer,

Pursuant to the California Public Records Act (Cal. Gov. Code § 7920 et seq.),
I respectfully request a digital copy (CSV or Excel) of the following building
permit records for the period **`<FROM_DATE>` through `<TO_DATE>`**:

For each permit, please include — to the extent the records are kept in this
form — the following fields:

| Column | Notes |
|---|---|
| `permit_number` | City's permit ID / case number |
| `permit_type` | e.g. New Construction, T.I., Remodel |
| `description` | Scope of work narrative |
| `address` | Site address (street + city) |
| `issued_date` | Date the permit was issued |
| `contractor_name` | Licensed contractor / firm of record |
| `contractor_license` | CSLB license number, if held |
| `contractor_phone` | If the city holds it on file |
| `contractor_email` | If the city holds it on file |

I am only requesting **records that are already maintained in this form**.
Please feel free to redact any information protected by Cal. Gov. Code
§ 7927.700 (personal information) or related exemptions.

If any portion of this request is unclear, or if a fee waiver or alternative
format would help, please contact me before proceeding. I prefer to receive
the response by email as a CSV or Excel attachment.

Thank you for your time.

Sincerely,
`<YOUR_NAME>`
`<YOUR_EMAIL>` · `<YOUR_PHONE>`

---

## 中文要点(给你自己看,不必发出去)

- **目标**:拿到 `<CITY>` 最近 `<FROM_DATE>`–`<TO_DATE>` 期间的所有建筑许可,**必须含承包商记录**(我们的硬要求 = 每行至少邮箱或电话)。
- **列**:permit_number, permit_type, description, address, issued_date, contractor_name, contractor_license, contractor_phone, contractor_email。
- **不要的**:房主姓名/申请人姓名(privacy);若同一份记录里同时有 owner 和 contractor,只提取 contractor 列即可。
- **格式**:**CSV 或 Excel**,邮件回复。不要 PDF 扫描件——CSV 直接可入管线。
- **法定时限**:加州法定 10 天回应(可延 14 天)。
- **费用**:加州只能收 直接复制成本——电子拷贝几乎免费。若对方报价高,问能否 fee waiver。
- **如果对方说"我们没有这种字段"**:请求他们导出他们 **有** 的字段(至少 permit_number + contractor_name 就够走 enrich 路径 B,CSLB 反查电话)。

---

## CSV mapping cheat-sheet

When a city returns a CSV with *different* column headings, write a tiny field
map in `config/cpra/portals.yaml` next to that city and rerun
`projectintel import-cpra`. The mapping mirrors `SocrataFieldMap`:

```yaml
field_map:
  permit_number: PermitID            # whatever the city calls it
  description: [Scope, WorkType]      # list = tried in order
  address: [Address, SiteAddress]
  project_date: [IssuedDate, IssueDate]
  city: City                         # null = fall back to jurisdiction
  company_name: ContractorName
  company_license: ContractorLicense
  company_phone: ContractorPhone
  company_email: ContractorEmail
```

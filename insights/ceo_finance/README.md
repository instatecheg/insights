### CEO Finance

An integrated module in the customized Insights app. Insights Admins open
`/ceo-company-sources` to register an ERPNext MariaDB database without writing SQL.
Credentials remain in Insights Data Sources, not in the source repository.

### Setup

Requires ERPNext and the customized Insights v3 branch containing this module,
`insights.api.dashboard_sync` and the query `locked_data_source` field.
Install Insights normally; no separate CEO Finance app or repository is needed.
The integrated functionality has been tested on Frappe/ERPNext v16 and Python 3.14.

An administrator configures **CEO Dashboard Settings** once: dashboard, combined
UNION ALL query, a company query whose dashboard charts serve as templates, and an
optional native Site DB freshness query. All must use the same workbook.
Run `insights.ceo_finance.api.adopt_existing_sources` once in an Administrator Frappe
context, then commit. This adopts the existing SQL sources without duplicating
cards. It standardizes their accounting rules and locks the managed queries.
Back up the existing dashboard and queries before adoption.

### Adding a source

1. Open Insights Data Sources, create an SSL-enabled MariaDB connection using a
   read-only ERPNext database user, and save it. Prefer a trusted CA certificate
   for verified SSL; encryption without a CA is not identity verification.
2. Return to Company Sources and click **Reload sources**.
3. Select the source and enter a unique display label.
4. Click **Check companies and SAR currency**. Review every company listed;
   registration includes **all companies in that database**.
5. Click **Register checked source** and confirm. Wait for **Registered**.
6. Reload the CEO dashboard and click **Refresh**. Combined cards, comparison,
   monthly trends, separate source cards and freshness status include the source.

The three accounting tables are fully imported before dashboard membership is
changed. Imports must have no row limits or import scripts. Failed registration
is explicit and leaves membership unchanged; correct the underlying issue in
Error Log/Data Store and click **Retry**. A functioning long-queue worker is
required. Registration is administrator-only and takes up to 30 minutes.

Database identity is deduplicated by host, port and database name, independent
of the connection's label or username. Different host aliases or database
copies cannot be detected as the same business ledger: review company names
before registration and never add a replicated copy of an existing source.
Records and source identities are immutable; source removal is not offered.
Database passwords/users can be rotated through Insights without changing
the database identity. Labels and accounting queries are managed, not manually
editable after registration.

### Accounting and refresh

All source companies must have SAR as their default currency. P&L uses GL debit/
credit in company currency, Income/Expense
accounts, and excludes cancelled GL, opening entries and Period Closing Voucher
transfers. Revenue is credit minus debit; expense is debit minus credit; profit
is revenue minus expense. No intercompany eliminations or currency conversion.
Companies with no qualifying GL in the selected period have no P&L rows.

### Company and period analysis

After `insights.ceo_finance.dashboard_setup.enable_analysis()` is applied, financial
source queries retain individual qualifying GL rows across all years through
today. The dashboard defaults to current YTD but can select any available year,
a month, or an inclusive custom date range using **Reporting Period**. Apply
the selection to recalculate every financial chart. **Company** selects the
source-qualified company label, so identically named companies on different
sites remain distinguishable. Future registered sources inherit these filters.
Matching controls beside **Separate Company Summaries** use the same filter
state as the controls at the top; changing either updates the entire dashboard.
Companies excluded by the selection display **No data**, not misleading zeroes.

### Detailed Profit and Loss inside Insights

**Profit and Loss - All Companies** is a separate dashboard, linked from the
CEO overview and configured in **CEO Dashboard Settings**.
The original ERPNext Profit and Loss Statement is not overridden or embedded.
Its company and period selections are independent of the CEO overview.
Empty Company selection means all companies, including after clearing selections.
Choose Current YTD, source-qualified fiscal years or an inclusive date range.
Finance Book, Cost Center, Project, source-specific accounting dimensions,
Periodicity, Accumulated Values, Report/Growth/Margin and Show zero values apply
to this dashboard. Use **Apply P&L Filters** after changing them.
Searchable checkbox dropdowns allow multiple company/dimension choices.
**Reset All Filters** returns to all companies/current YTD and clears advanced
filters. A period with no matching posted entries displays **No data**, not
misleading zero summaries; true zero balances with qualifying entries stay zero.
Currency remains SAR. Custom report templates are deliberately excluded because
the connected sources run different ERPNext versions.

Fiscal years and dimension choices are read from each source and qualified by
source label. Cost centers and tree accounting dimensions include descendants.
Include Default FB Entries follows ERPNext book filtering, including unassigned
entries; selecting a different book requires unchecking that option. With no
book selected, each company uses its own default book. Selected book, cost
center, project and dimension options only match their source, never another
database's identically named values.

This dashboard reads live source data on Apply and Refresh; unlike CEO Refresh,
it does not import warehouse tables or change source databases.
It uses the same cancelled/opening/
closing-entry exclusions as the CEO overview. Accumulated values reset on each
source fiscal year; summary cards use the final accumulated period instead of
summing running balances. Growth uses ERPNext's standard growth calculation.
Margin expresses each account as a percentage of that company's income.
The table retains account parents and children: do not sum both levels.
Companies and account groups start collapsed after loading/applying filters or
Refresh. Click a company name to reveal its root accounts, then click a parent
account name to reveal its children, continuing down the hierarchy.
Click the same name again to collapse it. Period amounts still open GL detail.
The Company Profit and Loss area has a company selector, three summary amounts
and an Income/Expense/Net Profit chart by the selected periodicity, with labelled
SAR axes, legend and exact-value tooltips. Periodicity defaults to Quarterly.
The selector defaults to **All companies (main filters)**: summary amounts are
combined, while the chart displays named company groups with three bars each
for the applied period (final-period totals when accumulated).
Only companies included by the applied filters appear. Individual choices are also limited
to that filtered set; selecting one changes this area's company, not the report
filters. If that company is excluded by new main filters, the chart returns to
All companies. A company comparison table, combined Revenue vs Expenses chart,
per-company profit trends and Net Profit comparison follow the same filters.
Charts use raw SAR amounts even in Growth/Margin table view. Accumulated charts
show running values; summary/comparison totals use the final period.
Zero-income margins are N/A; losses are shown in red in summaries and below
zero in charts. The CEO Main Consolidated Dashboard remains unchanged.
Click a period amount for GL and voucher references; detail is limited to
1,000 entries with an explicit warning to narrow the date range.

Click a financial card to open an account breakdown. Revenue is limited to
Income accounts; Expenses to Expense accounts; Net Profit and Profit Margin
show both categories with Revenue, Expenses and Net Profit amounts. Margin
is a ratio, not an additive account value. Double-click a breakdown amount for
matching GL rows, including posting date, account, debit, credit and voucher
type/number. The selected dashboard company/date filters apply at both levels.

Sync timestamps and the existing live Arena journal check are on the separate
**Finance Technical - Sync and Journals** dashboard, linked from the CEO view.
Refresh there reloads live checks; CEO Refresh imports the financial snapshot.
Journal checking remains Arena-specific rather than claiming all-source coverage.

Dashboard Refresh uses Insights' dependency discovery to import all registered
sources. After migration, this app restores the existing Insights stored-table
sync job to Hourly (site-wide), rather than creating another scheduler.
Existing individual company cards are retained; new cards summarize a **source
database**, whereas the comparison and company-series charts separate its companies.

### Dashboard branding

The CEO and P&L dashboards display the supplied clean Abaad Holding symbol and brand
name above the financial content, including in PNG exports. Branding is stored
on each Insights Dashboard v3 in **Brand Name** and **Brand Logo** (Attach Image).
Replace the attachment there to use a clean logo in future. Other dashboards
without branding retain their existing appearance.

### Arabic / English

Use the **English / العربية** selector beside Refresh on an Insights dashboard.
It saves an Insights-only preference for the signed-in user and reloads the
page. ERPNext's User language and the site language are not changed.
Arabic uses a right-to-left layout, translated dashboard headings, controls,
chart legends and financial table headings. Gregorian periods and SAR amounts
retain their accounting meaning; Western digits remain readable, with negative
signs isolated from RTL text. Company/account names and voucher identifiers
remain the actual source records rather than being renamed.
Applied P&L filters survive a language-switch reload; the main CEO filters
continue using their existing saved state. A user without an Insights preference
inherits their existing user/site language. Guests cannot save a preference.
Custom dashboard wording can be translated through the standard Frappe
translation catalog; the financial Arabic additions are in Insights' ar.csv.

### Validation

Run `insights.ceo_finance.tests.test_registration` with unittest in an initialized
Administrator Frappe context. No tests require remote credentials.

### Frappe Cloud private bench deployment

Deploy `https://github.com/instatecheg/insights`, branch `abaad-ceo-v3`.
This single repository contains the Insights extensions and CEO Finance module.
The branch is based on the tested upstream `version-3` release, not `develop`.

Keep the Insights upstream remote pointing to `frappe/insights`. Review and test
upstream updates on a staging site before merging; deploying stock Insights would
remove the frontend, finance APIs and schema extensions required by these dashboards.

1. Confirm the private bench supports the tested Frappe/ERPNext v16 and Python
   3.14 stack. Other runtimes have not been certified for the customization.
2. Grant Frappe Cloud access to the Insights fork and add `abaad-ceo-v3`
   to the private bench along with Frappe and ERPNext. Deploy to staging first.
   Install Insights, then run the normal site migration.
   Cloud builds frontend assets; generated assets are not versioned in Git.
3. Migrate site data separately. Code installation alone does not recreate the
   workbook, queries, charts, dashboards, CEO Dashboard Settings, registered
   sources or per-user language preferences. For a whole-site migration, use
   Frappe's supported backup/restore procedure with database and public/private
   files, and securely preserve the source site's encryption key so encrypted
   connection passwords remain usable. Never commit backups, site configuration,
   encryption keys or source credentials to the repository.
4. Do not restore a whole local site over an existing production ERPNext site.
   For an existing destination, plan a selective Insights/settings migration
   with reference remapping and credential re-entry instead.
5. Confirm the cloud site can reach every source database. Use read-only users,
   verified TLS and narrowly scoped firewall rules. The native Site DB source
   refers to the destination site's database after migration, not the old local
   site's database; review its company/data before accepting dashboard totals.
   Before migration imports fixtures, the native Site DB registration is rebound
   to the current site's database identity. Remote registrations are never
   rebound by this step and remain protected against connection changes.
6. Enable the scheduler and long-queue workers. CEO Refresh imports source
   tables; P&L reads live source data. Verify both paths and inspect failed jobs.
7. Reconcile per-company revenue, expenses and profit against the source
   ERPNext reports. Verify all-company and period filters, account/GL drill-down,
   private logo access, Arabic/English switching and permissions before
   switching production traffic.

Record the deployed commit revisions in the release/change record. Back up the
destination site before every migration; reverting app code alone does not undo
database or site-data changes.

### Migrating the retired standalone add-on

Back up the source database and public/private files before transferring ownership.
Finish all queued/importing company registrations first. On the existing source
bench, with this customized Insights branch deployed, run:

```bash
bench --site SITE execute insights.ceo_finance.migrate.migrate_legacy_app
bench --site SITE migrate
bench build --app insights
```

The transfer reloads the two existing DocTypes into the Insights module and removes
only the `ceo_finance` installed-app registration. It keeps the DocType names,
source rows, single settings, queries, dashboards and attachments unchanged.
It is idempotent. Do not use `uninstall-app ceo_finance`: uninstalling deletes
app-owned data. Complete this transfer on the source bench before creating the
backup for a one-app cloud restore, so the restored site does not require the
retired package. A restore made before transfer still needs the legacy app present
until ownership transfer completes.

Old browser bundles must be reloaded after deployment. New API and background-job
paths start with `insights.ceo_finance`. The source bench retains temporary legacy
wrappers for the transition; they are not a cloud dependency.

### License

The migrated finance module retains its [MIT notice](./LICENSE).
The Insights application retains its upstream GPLv3 license.

# Cost Analyzer: user guide

Live site: https://cost-analyzer-6lae.onrender.com (the first visit after a quiet period takes about a minute to wake up).

| # | Feature | What it does | How to use it |
|---|---|---|---|
| 1 | **Account** | Keeps your projects private. Nobody else can see them. | Click **Create one** on the sign-in page and choose a username and a password of 8+ characters. Next time, sign in. Use **Sign out** (top right) when done. |
| 2 | **Projects** | Each budget (a client job, your FYP, an event) is a separate project. | On the home page, type a name and click **Create project**. Click a project name to open it. |
| 3 | **Add an item by hand** | Records one cost line: what it is, its group, what you planned to spend and what you really spent. | In **Add a cost item**, fill in Item, Category (optional), Estimated cost and Actual cost, then click **Add item**. |
| 4 | **Upload Excel or CSV** | Imports a whole cost sheet at once. | In **Upload Excel / CSV**, choose the file. Pick **Add to existing items** or **Replace existing items**, then click **Upload**. Click **Download sample template** to see the expected layout. |
| 5 | **Remove an item** | Deletes one wrong line. | Click **Remove** at the end of that row. |
| 6 | **Automatic calculations** | Works out the variance (actual − estimated), % variance and a status for every item, every category and the whole project. | Nothing to do: the four boxes at the top, the item table and the **By category** table update as soon as you add data. Red = over budget, green = under, grey = within ±2%. |
| 7 | **Charts** | Shows estimated vs actual per category, the variance of every item, and where the money went. | Look below the totals. Hover over a bar or slice to see the exact number. |
| 8 | **Unusual expenses** | Points out lines that need checking: more than 25% over plan, money spent with no plan, or far out of line with the rest. | Scroll to **AI insights**. Each flagged item shows **High** or **Medium** and the reason. |
| 9 | **AI cost summary** | Writes a short report: overall position, what caused the overrun, unusual items and suggestions. | Click **Generate AI summary**. If you change the data later, a blue note asks you to click **Regenerate**. Always check the AI's numbers against the tables. |
| 10 | **PDF report** | A 3-page report with totals, the AI summary, flagged items, charts and all tables, ready to print or submit. | Click **Download PDF report** at the top of the project. |
| 11 | **Excel export** | The same results as a spreadsheet (4 sheets) for further work in Excel. | Click **Export to Excel**. |
| 12 | **Delete a project** | Removes the project and everything in it, permanently. | At the bottom of the project page, tick **I understand**, then click **Delete project**. |

## File formats that work

- **File types:** `.xlsx` (Excel) and `.csv`. Old `.xls` files must be saved as `.xlsx` first. Only the first sheet of an Excel file is read.
- **Separators:** comma, semicolon, tab or pipe. The app detects which one is used.
- **Column names:** any order, any capitals. Needed: an item column (`Item`, `Description`, `Expense` or `Name`), an estimate column (`Estimated`, `Estimate`, `Budget` or `Planned`) and an actual column (`Actual`, `Actual Cost`, `Spent` or `Actual Spend`). `Category` (or `Type` / `Group`) is optional. Any other columns are ignored.
- **Numbers:** `1200`, `1,200.50`, `$1,200`, `Rs 5,000`, `PKR 450` and `USD 100` all work. Negative numbers and text such as "approx 500" do not.
- **Limits:** 2 MB per file, 1,000 rows per file and per project.

## Messages you might see

| Message | Meaning |
|---|---|
| *Upload rejected: …* | The file isn't a real CSV/Excel file, is too big, is missing a column or has a bad value. The message says which row. Nothing was imported. |
| *Too many requests* | You did the same thing too quickly (for example 10 wrong passwords in 15 minutes, or more than 5 AI summaries a minute). Wait and try again. |
| *Not found* | The page doesn't exist, or the project belongs to another account. |
| *Request blocked* | The form was open too long or didn't come from this site. Refresh the page and try again. |

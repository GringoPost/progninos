# Showing Progniños on GringoPost

Every morning the daily run can copy the new report into one WordPress page. It only replaces that page's
content: it never creates, publishes or deletes anything. The page stays a draft until you publish it yourself.

Do this on the **staging site first** (staging-9654-mygringopost.wpcomstaging.com). When it looks right,
repeat steps 1–3 on gringopost.com.

Time needed: about 10 minutes. Cost: none.

## 1. Create an empty page

1. In WP Admin on the staging site: **Pages → Add New Page**.
2. Title: `Progniños` (or any title you like). Leave the content empty.
3. Click **Save draft**. Do not publish it.
4. Look at the address bar. It ends with something like `post.php?post=12345&action=edit`.
   The number (`12345`) is the **page ID**. Write it down.

If the page opens in Elementor, close Elementor and use the normal WordPress editor for this page: the report
arrives as a single Custom HTML block.

## 2. Create an Application Password

This is a separate password just for this job. You can delete it at any time, and the job stops.

1. In WP Admin: **Users → Profile** (your own profile; you need to be an Administrator or Editor so the charts
   are kept).
2. Scroll to **Application Passwords**.
3. Name: `Progniños GitHub`. Click **Add New Application Password**.
4. Copy the password it shows (it looks like `abcd efgh ijkl mnop qrst uvwx`). It is shown only once.
5. Also note your **username** (shown at the top of the profile page). It is not your email address.

Never send this password by email or chat. It goes straight into GitHub in the next step.

## 3. Put the four values into GitHub

In the repository on GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Add four secrets, one at a time:

| Name | Value |
|---|---|
| `WP_SITE` | `https://staging-9654-mygringopost.wpcomstaging.com` |
| `WP_USER` | your WordPress username |
| `WP_APP_PASSWORD` | the Application Password from step 2 |
| `WP_PAGE_ID` | the page ID from step 1 |

GitHub hides secrets after saving them. Nobody, including us, can read them back.

## 4. Try it

1. On GitHub: **Actions → Daily Progniños run → Run workflow**.
2. When it finishes, open the run and look at the last step, **Update the WordPress page**. It should say
   `Updated page 12345 (draft)`.
3. In WP Admin, open the page and click **Preview**.

From then on the page updates itself every morning at about 07:00 Cuenca time.

### "Quality checks failed; the page keeps yesterday's report"

That is deliberate. If anything looks wrong (missing data, impossible numbers), the page is not touched that
day. Until about November 11 one check will fail because the forecast archive does not yet cover a full week.

To test before then, add a repository **variable** (Settings → Secrets and variables → Actions → **Variables**
tab): name `WP_PUBLISH_WITH_WARNINGS`, value `1`. Delete it again after testing.

### "WordPress removed the charts or styles"

The WordPress user in `WP_USER` needs to be an Administrator or Editor. Lower roles are not allowed to save
charts in a page.

## 5. Moving to the live site

When the staging page looks right:

1. Repeat steps 1 and 2 on gringopost.com.
2. In GitHub, edit the secrets `WP_SITE` (`https://gringopost.com`), `WP_APP_PASSWORD` and `WP_PAGE_ID`.
3. Run the workflow once, check the draft, and publish the page in WordPress when you are ready.

## Turning it off

Delete the Application Password in WordPress (Users → Profile), or delete the `WP_APP_PASSWORD` secret on
GitHub. The page keeps its last report.

# Showing Progniños on GringoPost

The page loads the latest report straight from this repository every time someone opens it. WordPress does
not need a password, and the daily run never touches the site.

How it works:

1. Every morning the daily run writes `output/latest/site.html`, but only if all automatic checks passed.
   On a bad day the previous report stays.
2. The Progniños page on GringoPost contains one small Custom HTML block (below). When a visitor opens the
   page, it fetches `output/latest/site.html` and shows it with the site's own fonts and colours.

This needs the repository to be **public** so the page can read the file. Nothing secret is stored in it.

## Add the block (once)

1. In WP Admin, open the Progniños page in the normal WordPress editor (not Elementor).
2. Click **+**, search for **Custom HTML**, and add it.
3. Paste this into the block (the same code is in [`snippet.html`](snippet.html)):

```html
<div id="progninos-report"><p>Loading the latest Cuenca weather report…</p></div>
<script>
(function () {
  var box = document.getElementById('progninos-report');
  var url = 'https://raw.githubusercontent.com/GringoPost/progninos/main/output/latest/site.html';
  fetch(url, { cache: 'no-cache' })
    .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
    .then(function (html) {
      box.innerHTML = html;
      var rep = box.querySelector('[data-report-date]');
      if (!rep) { return; }
      // Cuenca is UTC-5 all year. A new report normally arrives shortly after 07:00 there.
      var now = new Date(Date.now() - 5 * 3600 * 1000);
      var yesterday = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
      var date = rep.getAttribute('data-report-date');
      if (date < yesterday || (date === yesterday && now.getUTCHours() >= 9)) {
        var shown = new Date(date + 'T12:00:00Z').toLocaleDateString('en-US',
          { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
        var note = document.createElement('p');
        note.style.cssText = 'background:#fff4e5;border-left:4px solid #e08a2c;padding:8px 12px;color:#222';
        note.textContent = 'This is the report from ' + shown + '. Today’s update was held back ' +
          'because some data did not pass our automatic checks.';
        rep.insertBefore(note, rep.querySelector('.sub'));
      }
    })
    .catch(function () {
      box.innerHTML = '<p>The weather report is not available right now. Please try again later.</p>';
    });
})();
</script>
```

4. Click **Save draft**, then **Preview**. Publish the page when you are happy with it.

Do it on the staging site first if you prefer; the block is the same on both sites.

## Notes

- If the page is showing an older report (because a later update failed its checks), it says so at the top, with the report's date.
- Readers see temperatures, rain / no rain, monthly rain against the normal and NOAA's El Niño status. Daily rain amounts and accuracy scores stay internal until about three months of records exist.

- GitHub caches the file for about five minutes, so a new report can take a few minutes to appear.
- Search engines may index the page text less well than normal page text, because it is loaded by script.
- The older password-based method (`publish_wp.py`) is no longer used. WordPress.com ignored the
  Application Password, so it never worked. Any Application Password created for it can be deleted
  (Users → Profile → Application Passwords → Revoke).

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
3. Paste this into the block:

```html
<div id="progninos-report"><p>Loading the latest Cuenca weather report…</p></div>
<script>
(function () {
  var box = document.getElementById('progninos-report');
  fetch('https://raw.githubusercontent.com/GringoPost/progninos/main/output/latest/site.html', { cache: 'no-cache' })
    .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
    .then(function (html) { box.innerHTML = html; })
    .catch(function () {
      box.innerHTML = '<p>The weather report is not available right now. Please try again later.</p>';
    });
})();
</script>
```

4. Click **Save draft**, then **Preview**. Publish the page when you are happy with it.

Do it on the staging site first if you prefer; the block is the same on both sites.

## Notes

- GitHub caches the file for about five minutes, so a new report can take a few minutes to appear.
- Search engines may index the page text less well than normal page text, because it is loaded by script.
- The older password-based method (`publish_wp.py`) is no longer used. WordPress.com ignored the
  Application Password, so it never worked. Any Application Password created for it can be deleted
  (Users → Profile → Application Passwords → Revoke).

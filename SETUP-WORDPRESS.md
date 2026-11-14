# Showing Progniños on GringoPost

The page loads the latest report straight from this repository every time someone opens it. WordPress does
not need a password, and the daily run never touches the site.

How it works:

1. Every day the run writes `output/latest/<city>.html` for each city, but only if all automatic checks passed.
   On a bad day the previous report stays.
2. One Progniños page on GringoPost serves every city. It contains one small Custom HTML block (below).
   Visitors from Mexico start on Ajijic and visitors from Vietnam on Da Nang, using the site's own country
   setting (the `gpsm_prefs_v1` cookie) or the browser's time zone. Everyone else starts on Cuenca. Buttons
   at the top switch city, and a visitor's choice is remembered on their device.

This needs the repository to be **public** so the page can read the file. Nothing secret is stored in it.

## Add the block (once)

1. In WP Admin, open the Progniños page in the normal WordPress editor (not Elementor).
2. Click **+**, search for **Custom HTML**, and add it.
3. Paste this into the block (the same code is in [`snippet.html`](snippet.html)):

```html
<!-- Progniños: one page for all cities. Visitors from Mexico see Ajijic and from Vietnam see Da Nang,
     using GringoPost's own country setting (cookie gpsm_prefs_v1) or, failing that, the browser's time zone.
     Everyone else starts on Cuenca. A visitor's own choice is remembered on their device. -->
<div id="progninos-report"><p>Loading the latest weather report…</p></div>
<script>
(function () {
  var CITIES = { cuenca: 'Cuenca', quito: 'Quito', ajijic: 'Ajijic', danang: 'Da Nang' };
  var BY_COUNTRY = { mexico: 'ajijic', vietnam: 'danang', ecuador: 'cuenca' };
  var BASE = 'https://raw.githubusercontent.com/GringoPost/progninos/main/output/latest/';
  var box = document.getElementById('progninos-report');

  function saved() {
    try { var c = localStorage.getItem('progninos_city'); return CITIES[c] ? c : ''; } catch (e) { return ''; }
  }
  function fromSite() {
    var m = document.cookie.match(/(?:^|; )gpsm_prefs_v1=([^;]*)/);
    if (!m) { return ''; }
    try {
      var p = JSON.parse(decodeURIComponent(m[1]));
      var city = String(p.t || '').toLowerCase().replace(/[^a-z]/g, '');
      if (CITIES[city]) { return city; }
      return BY_COUNTRY[String(p.c || '').toLowerCase()] || '';
    } catch (e) { return ''; }
  }
  function fromTimeZone() {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      if (/^Asia\/(Ho_Chi_Minh|Saigon)$/.test(tz)) { return 'danang'; }
      if (/^America\/(Mexico_City|Cancun|Merida|Monterrey|Mazatlan|Chihuahua|Hermosillo|Tijuana|Bahia_Banderas|Matamoros|Ojinaga)$/.test(tz)) { return 'ajijic'; }
    } catch (e) { /* ignore */ }
    return '';
  }

  function switcher(active) {
    var nav = document.createElement('p');
    nav.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;max-width:860px;margin:0 auto 12px;padding:0 16px;box-sizing:border-box';
    Object.keys(CITIES).forEach(function (slug) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = CITIES[slug];
      b.setAttribute('aria-pressed', slug === active ? 'true' : 'false');
      b.style.cssText = 'padding:8px 18px;border-radius:99px;border:1px solid #2f6fb2;cursor:pointer;' +
        'font-size:16px;line-height:1.2;font-weight:600;min-height:40px;text-transform:none;letter-spacing:0;' +
        (slug === active ? 'background:#2f6fb2;color:#fff' : 'background:#fff;color:#2f6fb2');
      // The site theme shrinks buttons with !important rules, so these need !important too.
      ['font-size:16px', 'line-height:1.2', 'padding:8px 18px'].forEach(function (rule) {
        var kv = rule.split(':');
        b.style.setProperty(kv[0], kv[1], 'important');
      });
      b.onclick = function () {
        try { localStorage.setItem('progninos_city', slug); } catch (e) { /* ignore */ }
        load(slug);
      };
      nav.appendChild(b);
    });
    return nav;
  }

  function load(city) {
    box.innerHTML = '';
    box.appendChild(switcher(city));
    var body = document.createElement('div');
    body.innerHTML = '<p>Loading the latest report for ' + CITIES[city] + '…</p>';
    box.appendChild(body);
    fetch(BASE + city + '.html', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
      .then(function (html) {
        body.innerHTML = html;
        var rep = body.querySelector('[data-report-date]');
        if (!rep) { return; }
        // A new report arrives once a day. Older than 30 hours means at least one update was held back.
        var made = Date.parse(rep.getAttribute('data-generated') || '');
        if (made && Date.now() - made > 30 * 3600 * 1000) {
          var shown = new Date(rep.getAttribute('data-report-date') + 'T12:00:00Z').toLocaleDateString('en-US',
            { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
          var note = document.createElement('p');
          note.style.cssText = 'background:#fff4e5;border-left:4px solid #e08a2c;padding:8px 12px;color:#222';
          note.textContent = 'This is the report from ' + shown + '. The latest update was held back ' +
            'because some data did not pass our automatic checks.';
          rep.insertBefore(note, rep.querySelector('.sub'));
        }
      })
      .catch(function () {
        body.innerHTML = '<p>The report for ' + CITIES[city] + ' is not available right now. ' +
          'New cities need about a week of saved forecasts before their first report. Please try again later.</p>';
      });
  }

  load(saved() || fromSite() || fromTimeZone() || 'cuenca');
})();
</script>
```

4. Click **Save draft**, then **Preview**. Publish the page when you are happy with it.

Do it on the staging site first if you prefer; the block is the same on both sites.

## Notes

- If the page is showing a report more than 30 hours old (because a later update failed its checks), it says so at the top, with the report's date.
- A new city shows "not available" for about its first eight days, until its own forecast archive covers a full week.
- Adding a city is one entry in `LOCATIONS` at the top of `progninos.py`.
- Readers see temperatures, rain / no rain, monthly rain against the normal and NOAA's El Niño status. Daily rain amounts and accuracy scores stay internal until about three months of records exist.

- GitHub caches the file for about five minutes, so a new report can take a few minutes to appear.
- Search engines may index the page text less well than normal page text, because it is loaded by script.
- The older password-based method (`publish_wp.py`) is no longer used. WordPress.com ignored the
  Application Password, so it never worked. Any Application Password created for it can be deleted
  (Users → Profile → Application Passwords → Revoke).

# /assets

General-purpose folder for anything that doesn't have its own spot yet —
extra SVGs, icons, a favicon, whatever. Drop files in here and they'll
sit alongside the site, ready to reference.

This is separate from the two folders the site's code already points to:

- `/hero-logos/` — the Hero's swappable logo SVGs (see `HERO_LOGO_CONFIG`
  near the top of the `<script>` in index.html)
- `/projects/` — the Process archive's project media (see `buildProjects()`
  in the same script)

Nothing in this file needs to change for those two to work — they're
independent, root-level folders next to `index.html`, not nested under
`/assets`. If you want something from `/assets` actually wired into the
site (a favicon, an og:image, a custom font file), just say so and I'll
point the relevant bit of code at it.

Expected layout once you're hosting this (e.g. on GitHub Pages):

```
/
  index.html
  hero-logos/
    logo-01.svg
  projects/
    001.png
    013.mp4
  assets/
    (whatever you drop here)
```

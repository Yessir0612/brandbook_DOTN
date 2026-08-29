# /hero-logos

Drop your SVGs here, named to match HERO_LOGO_CONFIG.srcs in index.html
(currently logo-01.svg, logo-02.svg, logo-03.svg — only `active: 0`
actually shows right now, the rest are just ready for later).

The Hero keeps showing its animated blob-with-eyes until a file at
`active`'s path actually loads — so nothing breaks in the meantime.

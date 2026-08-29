/* ============================================================
   BLOT SHARED SYSTEM — JS
   Loaded by both index.html and process.html, after each page's
   own script. Covers: theme switcher behavior and the cross-page
   organic-blob transition.
   ============================================================ */
(function(){
  "use strict";

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var THEME_KEY = 'blot-theme';
  var ORIGIN_KEY = 'blot-transition-origin';
  var BLOT_PATH = 'M50 8c-4 20-32 34-32 58a32 32 0 0 0 64 0c0-24-28-38-32-58z';

  /* ---------------- Theme switcher ---------------- */
  function currentTheme(){
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function applyTheme(theme){
    document.documentElement.setAttribute('data-theme', theme);
    try{ localStorage.setItem(THEME_KEY, theme); }catch(e){}
    document.querySelectorAll('.theme-switch button').forEach(function(btn){
      var pressed = btn.getAttribute('data-theme-set') === theme;
      btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    });
    var meta = document.querySelector('meta[name="theme-color"]');
    if(meta) meta.setAttribute('content', theme === 'dark' ? '#14151A' : '#EFEDE4');
  }

  function themeSwitchMarkup(){
    return '<div class="theme-switch" role="group" aria-label="Theme">' +
      '<button type="button" data-theme-set="light">Light</button>' +
      '<span class="theme-switch-sep">/</span>' +
      '<button type="button" data-theme-set="dark">Dark</button>' +
      '</div>';
  }

  function mountThemeSwitch(container){
    if(!container) return;
    container.insertAdjacentHTML('beforeend', themeSwitchMarkup());
  }

  var headerNav = document.querySelector('.site-header .site-nav');
  var mobileMenuEl = document.getElementById('mobileMenu');
  if(headerNav) mountThemeSwitch(headerNav);
  if(mobileMenuEl) mountThemeSwitch(mobileMenuEl);

  document.querySelectorAll('.theme-switch button').forEach(function(btn){
    btn.addEventListener('click', function(){ applyTheme(btn.getAttribute('data-theme-set')); });
  });
  applyTheme(currentTheme()); // sync aria-pressed on the buttons we just mounted

  /* ---------------- Header progressive blur (markup only — CSS does the rest) ---------------- */
  var header = document.querySelector('.site-header');
  if(header && !header.querySelector('.header-blur')){
    var blurWrap = document.createElement('div');
    blurWrap.className = 'header-blur';
    blurWrap.setAttribute('aria-hidden','true');
    blurWrap.innerHTML = '<span></span><span></span><span></span><span></span>';
    header.insertBefore(blurWrap, header.firstChild);
  }

  /* ---------------- Page transition ---------------- */
  if(reduceMotion) return; // native navigation only — no overlay, nothing to wire up

  function isInternalPageLink(a){
    var href = a.getAttribute('href') || '';
    if(!href || href.charAt(0) === '#') return false;
    if(href.indexOf('mailto:') === 0 || href.indexOf('tel:') === 0) return false;
    if(/^https?:\/\//i.test(href) || href.indexOf('//') === 0) return false;
    if(a.target === '_blank' || a.hasAttribute('download')) return false;
    var targetFile = href.split('#')[0].split('?')[0].split('/').pop();
    if(!/\.html$/i.test(targetFile)) return false;
    var currentFile = window.location.pathname.split('/').pop() || 'index.html';
    if(targetFile === currentFile) return false; // already here — no transition needed
    return true;
  }

  function ensureOverlay(){
    var el = document.getElementById('pageTransition');
    if(el) return el;
    el = document.createElement('div');
    el.id = 'pageTransition';
    el.className = 'page-transition';
    el.setAttribute('aria-hidden','true');
    el.innerHTML = '<svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d="'+BLOT_PATH+'"/></svg>';
    document.body.appendChild(el);
    return el;
  }

  function coverScale(el, originX, originY){
    var rect = el.getBoundingClientRect();
    var halfW = (rect.width || 110) / 2;
    var dx = Math.max(originX, window.innerWidth - originX);
    var dy = Math.max(originY, window.innerHeight - originY);
    var maxDist = Math.sqrt(dx*dx + dy*dy);
    var tightestRatio = 0.62; // this path's tightest reach from center, as a fraction of its own box — same figure used for the loader's cover-scale
    var overscan = 1.25;
    return Math.max((maxDist * overscan) / (halfW * tightestRatio), 6);
  }

  function playExit(originX, originY, onDone){
    var el = ensureOverlay();
    el.classList.remove('is-animating');
    el.style.left = originX + 'px';
    el.style.top = originY + 'px';
    el.style.transform = 'translate(-50%,-50%) scale(0)';
    void el.offsetWidth; // force the 0-scale state to register before animating
    var scale = coverScale(el, originX, originY);
    requestAnimationFrame(function(){
      el.classList.add('is-animating');
      el.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
    });
    var done = false;
    function finish(){ if(done) return; done = true; onDone(); }
    el.addEventListener('transitionend', finish, {once:true});
    setTimeout(finish, 750); // safety net in case transitionend doesn't fire
  }

  function playEnter(originX, originY){
    var el = ensureOverlay();
    var scale = coverScale(el, originX, originY);
    el.classList.remove('is-animating');
    el.style.left = originX + 'px';
    el.style.top = originY + 'px';
    el.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
    void el.offsetWidth;
    requestAnimationFrame(function(){
      el.classList.add('is-animating');
      requestAnimationFrame(function(){
        el.style.transform = 'translate(-50%,-50%) scale(0)';
      });
    });
    setTimeout(function(){
      document.documentElement.removeAttribute('data-incoming-transition');
      if(el && el.parentNode) el.parentNode.removeChild(el);
    }, 750);
  }

  document.addEventListener('click', function(e){
    if(e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest ? e.target.closest('a') : null;
    if(!a || !isInternalPageLink(a)) return;
    e.preventDefault();
    var href = a.getAttribute('href');
    var rect = a.getBoundingClientRect();
    var originX = e.clientX || (rect.left + rect.width/2);
    var originY = e.clientY || (rect.top + rect.height/2);
    try{ sessionStorage.setItem(ORIGIN_KEY, JSON.stringify({x:originX, y:originY})); }catch(err){}
    playExit(originX, originY, function(){ window.location.href = href; });
  });

  // Arriving here as a transition target: the inline <head> snippet already positioned
  // the overlay via CSS so there's no gap — this just plays the shrink-away reveal.
  if(document.documentElement.getAttribute('data-incoming-transition') === 'true'){
    var raw = null;
    try{ raw = sessionStorage.getItem(ORIGIN_KEY); sessionStorage.removeItem(ORIGIN_KEY); }catch(e){}
    var origin = null;
    try{ origin = raw ? JSON.parse(raw) : null; }catch(e){}
    var ox = (origin && typeof origin.x === 'number') ? origin.x : window.innerWidth/2;
    var oy = (origin && typeof origin.y === 'number') ? origin.y : window.innerHeight/2;
    playEnter(ox, oy);
  }

})();

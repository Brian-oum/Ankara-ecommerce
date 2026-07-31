/* Bella — hero interactions
   1) Count the hero stats up from 0 the first time they scroll into view.
   2) Pause the crossfading hero image while the browser tab is hidden.
   3) Fade + rise each section up into place as it's scrolled into view.
   4) Cycle client testimonials one at a time, with dot navigation.
   All four respect prefers-reduced-motion. */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- preloader: logo fill-up + typing tagline --------------------
     The fill animation itself runs in CSS (see .preloader-logo-fill).
     This just types a rotating business tagline underneath it, and
     removes the overlay once the page has actually finished loading
     (never before the fill animation has had a chance to finish). --- */
  (function () {
    var preloader = document.getElementById("preloader");
    if (!preloader) return;

    var typeEl = document.getElementById("preloaderTypeText");
    var phrases = [
      "Where beauty meets artistry",
      "Handcrafted Ankara, made for you",
      "Your best look, every day"
    ];
    var typeTimer = null;

    function typeLoop() {
      var phraseIndex = 0;
      var charIndex = 0;
      var deleting = false;

      function tick() {
        var current = phrases[phraseIndex];

        if (!deleting) {
          charIndex++;
          typeEl.textContent = current.slice(0, charIndex);
          if (charIndex === current.length) {
            deleting = true;
            typeTimer = window.setTimeout(tick, 1400);
            return;
          }
          typeTimer = window.setTimeout(tick, 45);
        } else {
          charIndex--;
          typeEl.textContent = current.slice(0, charIndex);
          if (charIndex === 0) {
            deleting = false;
            phraseIndex = (phraseIndex + 1) % phrases.length;
            typeTimer = window.setTimeout(tick, 300);
            return;
          }
          typeTimer = window.setTimeout(tick, 22);
        }
      }

      tick();
    }

    if (typeEl) {
      if (reduceMotion) {
        typeEl.textContent = phrases[0];
      } else {
        typeLoop();
      }
    }

    // Keep the overlay up for at least as long as the fill animation
    // (2.1s in CSS) so it never flashes off before the logo has formed.
    var MIN_VISIBLE = reduceMotion ? 300 : 2200;
    var shownAt = Date.now();

    function hidePreloader() {
      var wait = Math.max(MIN_VISIBLE - (Date.now() - shownAt), 0);
      window.setTimeout(function () {
        preloader.classList.add("is-hidden");
        document.body.classList.remove("preloader-lock");
        if (typeTimer) window.clearTimeout(typeTimer);

        preloader.addEventListener("transitionend", function handler() {
          preloader.remove();
          preloader.removeEventListener("transitionend", handler);
        });
      }, wait);
    }

    if (document.readyState === "complete") {
      hidePreloader();
    } else {
      window.addEventListener("load", hidePreloader);
    }
  })();

  /* ---- stat counters ---------------------------------------------- */
  function animateCount(el) {
    var target = parseInt(el.getAttribute("data-count"), 10) || 0;

    if (reduceMotion || target === 0) {
      el.textContent = target + "+";
      return;
    }

    var duration = 1100;
    var start = null;

    function step(timestamp) {
      if (start === null) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      // ease-out so the count settles rather than stopping abruptly
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.floor(eased * target) + "+";
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        el.textContent = target + "+";
      }
    }

    window.requestAnimationFrame(step);
  }

  var statEls = document.querySelectorAll(".hero-stat .num[data-count]");

  if (statEls.length) {
    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(
        function (entries, obs) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              animateCount(entry.target);
              obs.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.4 }
      );
      statEls.forEach(function (el) { observer.observe(el); });
    } else {
      // no IntersectionObserver support: just fill the numbers in
      statEls.forEach(function (el) { el.textContent = el.getAttribute("data-count") + "+"; });
    }
  }

  /* ---- "Watch Our Studio" trigger ----------------------------------
     Placeholder hook: wire this up to a modal / lightbox video player
     once studio footage is ready. */
  var watchBtn = document.querySelector("[data-video-trigger]");
  if (watchBtn) {
    watchBtn.addEventListener("click", function () {
      console.log("Play studio video");
    });
  }

  /* ---- pause the hero image crossfade when the tab isn't visible --- */
  var heroBlob = document.querySelector(".hero-blob");

  if (heroBlob) {
    document.addEventListener("visibilitychange", function () {
      var state = document.hidden ? "paused" : "running";
      heroBlob.querySelectorAll(".cycle-slide").forEach(function (slide) {
        slide.style.animationPlayState = state;
      });
    });
  }

  /* ---- partners row: fade all logo images in together the first time
     the row scrolls into view. .in-view is added to the row once, and
     every .partner-logo transitions at the same time (see style.css). */
  var partnersRow = document.querySelector(".partners-row");

  if (partnersRow) {
    if (reduceMotion) {
      partnersRow.classList.add("in-view");
    } else if ("IntersectionObserver" in window) {
      var partnersObserver = new IntersectionObserver(
        function (entries, obs) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("in-view");
              obs.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.3 }
      );
      partnersObserver.observe(partnersRow);
    } else {
      partnersRow.classList.add("in-view");
    }
  }

  /* ---- fade-up reveal for sections as they scroll into view -------- */
  var fadeEls = document.querySelectorAll(".fade-up");

  if (fadeEls.length) {
    if (reduceMotion) {
      // motion is disabled system-wide: show everything immediately,
      // the CSS media query already strips the transition too.
      fadeEls.forEach(function (el) { el.classList.add("in-view"); });
    } else if ("IntersectionObserver" in window) {
      var revealObserver = new IntersectionObserver(
        function (entries, obs) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("in-view");
              obs.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15, rootMargin: "0px 0px -80px 0px" }
      );
      fadeEls.forEach(function (el) { revealObserver.observe(el); });
    } else {
      // no IntersectionObserver support: just show the sections
      fadeEls.forEach(function (el) { el.classList.add("in-view"); });
    }
  }

  /* ---- testimonial slideshow: one review at a time ------------------
     The number of slides is set by the template (top_product_reviews),
     so this builds its dot navigation dynamically instead of assuming
     a fixed count. Advances every 6s, pauses on hover/focus and while
     the tab is hidden, and is fully inert under reduced motion. */
  (function () {
    var wrap = document.getElementById("testimonialSlideshow");
    var track = document.getElementById("testimonialSlides");
    if (!wrap || !track) return;

    var slides = track.querySelectorAll(".testimonial-slide");
    if (slides.length < 2) {
      // nothing to cycle through
      if (slides.length === 1) slides[0].classList.add("is-active");
      return;
    }

    var current = 0;
    var INTERVAL = 6000;
    var timer = null;

    function goTo(index) {
      slides[current].classList.remove("is-active");
      current = (index + slides.length) % slides.length;
      slides[current].classList.add("is-active");

      if (dots) {
        dots.forEach(function (dot, i) {
          dot.classList.toggle("is-active", i === current);
        });
      }
    }

    slides[0].classList.add("is-active");

    // dot navigation, built to match however many slides rendered
    var dots = null;
    if (!reduceMotion) {
      var dotsEl = document.createElement("div");
      dotsEl.className = "testimonial-dots";
      dots = [];
      slides.forEach(function (_, i) {
        var dot = document.createElement("button");
        dot.type = "button";
        dot.setAttribute("aria-label", "Show review " + (i + 1) + " of " + slides.length);
        if (i === 0) dot.classList.add("is-active");
        dot.addEventListener("click", function () {
          goTo(i);
          restart();
        });
        dots.push(dot);
        dotsEl.appendChild(dot);
      });
      wrap.appendChild(dotsEl);
    }

    if (reduceMotion) return; // static: first slide only, no auto-advance

    function start() {
      timer = window.setInterval(function () { goTo(current + 1); }, INTERVAL);
    }
    function stop() {
      if (timer) { window.clearInterval(timer); timer = null; }
    }
    function restart() { stop(); start(); }

    start();

    wrap.addEventListener("mouseenter", stop);
    wrap.addEventListener("mouseleave", start);
    wrap.addEventListener("focusin", stop);
    wrap.addEventListener("focusout", start);

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stop();
      } else {
        start();
      }
    });
  })();
})();
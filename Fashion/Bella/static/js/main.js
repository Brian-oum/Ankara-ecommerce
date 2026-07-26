/* Bella — hero interactions
   1) Count the hero stats up from 0 the first time they scroll into view.
   2) Pause the crossfading hero image while the browser tab is hidden.
   3) Fade + rise each section up into place as it's scrolled into view.
   All three respect prefers-reduced-motion. */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
})();
/**
 * deck.js — pointer and keyboard controller for the triage card deck.
 *
 * Server contract (do not change the markup):
 *   <div id="deck"> .deck-card.is-active[data-remaining] > .deck-photo,
 *     button[data-decision="no|maybe|yes"], a.deck-open </div>
 *   <span id="deck-progress">, <div id="toast"> with an optional .undo button.
 *
 * HTMX dependency: this file performs no network I/O. A decision always ends
 * with `.click()` on the card's own [data-decision] button, so HTMX reads the
 * hx-post/hx-vals attributes and appends the next card to #deck
 * (hx-swap="beforeend"). deck.js only animates and removes the resolved card,
 * keeping the DOM in sync with what the server appends.
 *
 * CSS contract: `.dragging`, `.snap-back`, `.flying` and `.fly-left/right/up`
 * provide the transitions/transforms; `.drag-left/right/up` plus the card's
 * temporary `data-decision` drive the drag cue. `.deck-card` should keep
 * `touch-action` set so horizontal swipes are not stolen by page scrolling.
 *
 * Keyboard: left = no, right = yes, up = maybe, z = undo, o = open listing.
 * No dependencies, no globals, no inline styles other than transform and
 * --drag-progress.
 */
(() => {
  'use strict';

  // ------------------------------------------------------------------ config

  const FLY_OUT_MS = 260;
  const SNAP_BACK_TIMEOUT_MS = 400; // fallback when transitionend never fires
  const CLICK_GUARD_MS = 400;
  const DRAG_SLOP_PX = 8;
  const INTENT_MIN_PX = 12;
  const ROTATION_DEG = 20;
  const THRESHOLD_X_RATIO = 0.35;
  const THRESHOLD_Y_RATIO = 0.25;

  const CARD_SELECTOR = '.deck-card';
  const PHOTO_SELECTOR = '.deck-photo';
  const OPEN_SELECTOR = '.deck-open';
  const DECISION_BUTTON_SELECTOR = 'button[data-decision]';
  const PROGRESS_ID = 'deck-progress';
  const TOAST_ID = 'toast';

  const CLASS_ACTIVE = 'is-active';
  const CLASS_DRAGGING = 'dragging';
  const CLASS_SNAP_BACK = 'snap-back';
  const CLASS_FLYING = 'flying';
  const CLASS_EMPTY = 'deck-empty';

  const FLY_CLASS = {
    no: 'fly-left',
    maybe: 'fly-up',
    yes: 'fly-right',
  };

  // Consumed by deck.css while dragging (outline cue for the pending decision).
  const DRAG_CLASS = {
    no: 'drag-left',
    maybe: 'drag-up',
    yes: 'drag-right',
  };

  const KEY_DECISION = {
    ArrowLeft: 'no',
    ArrowRight: 'yes',
    ArrowUp: 'maybe',
  };

  // ------------------------------------------------------------------- state

  let deck = null;
  let drag = null; // active pointer gesture, null when idle
  let prefetchedImage = null;
  let suppressNextClick = false;
  let clickGuardTimer = 0;
  const snapBacks = new WeakMap(); // card -> cleanup for the running snap-back

  // --------------------------------------------------------------- bootstrap

  function init() {
    const element = document.getElementById('deck');
    if (!element) return; // feature is not present on this page

    deck = element;
    deck.addEventListener('pointerdown', onPointerDown);
    deck.addEventListener('pointermove', onPointerMove, { passive: false });
    deck.addEventListener('pointerup', onPointerUp);
    deck.addEventListener('pointercancel', onPointerCancel);
    deck.addEventListener('dragstart', onDragStart);
    deck.addEventListener('click', onDeckClick, true);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('click', onDocumentClick, true);

    // Cards are prepended by the server (hx-swap="afterbegin"), so the first child is the top
    // card. Normalising after every swap keeps exactly one card interactive.
    document.body.addEventListener('htmx:afterSwap', function (event) {
      if (event.target === deck) normaliseDeck();
    });

    normaliseDeck();
    prefetchNextPhoto();
  }

  function normaliseDeck() {
    if (!deck || !deck.isConnected) return;

    const cards = deck.querySelectorAll(CARD_SELECTOR);
    cards.forEach(function (card, index) {
      card.classList.toggle(CLASS_ACTIVE, index === 0);
    });

    if (cards.length === 0) {
      deck.classList.add(CLASS_EMPTY);
      return;
    }
    deck.classList.remove(CLASS_EMPTY);
    updateProgress(cards[0]);
    prefetchNextPhoto();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }

  // ----------------------------------------------------------------- pointer

  function onDragStart(event) {
    // The card face is an <img>; the native image drag would steal the gesture.
    event.preventDefault();
  }

  function onPointerDown(event) {
    if (!deck || drag) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;

    const card = event.target instanceof Element
      ? event.target.closest(CARD_SELECTOR)
      : null;
    if (!card || !deck.contains(card)) return;
    if (!card.classList.contains(CLASS_ACTIVE)) return;
    if (card.classList.contains(CLASS_FLYING)) return;

    const rect = card.getBoundingClientRect();
    const width = card.offsetWidth || rect.width || 1;
    const height = card.offsetHeight || rect.height || 1;

    drag = {
      card,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      thresholds: {
        x: Math.max(1, width * THRESHOLD_X_RATIO),
        y: Math.max(1, height * THRESHOLD_Y_RATIO),
      },
      moved: false,
      captured: false,
      intent: null,
    };
  }

  function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;

    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;

    if (!drag.moved) {
      if (Math.hypot(dx, dy) < DRAG_SLOP_PX) return; // still a tap
      beginDrag(drag);
    }

    event.preventDefault();
    updateDrag(drag, dx, dy);
  }

  function onPointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;

    const state = drag;
    drag = null;

    // A tap: leave native clicks on buttons and links untouched.
    if (!state.moved) return;

    event.preventDefault();
    releasePointerCapture(state);
    armClickGuard();

    const dx = event.clientX - state.startX;
    const dy = event.clientY - state.startY;
    const decision = decideFromDrag(dx, dy, state.thresholds);

    if (decision) {
      commitDecision(state.card, decision);
    } else {
      snapBack(state.card);
    }
  }

  function onPointerCancel(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;

    const state = drag;
    drag = null;
    releasePointerCapture(state);
    if (state.moved) snapBack(state.card);
  }

  function beginDrag(state) {
    const { card } = state;
    state.moved = true;

    cancelSnapBack(card);
    card.classList.remove(CLASS_SNAP_BACK);
    card.classList.add(CLASS_DRAGGING);
    card.style.transform = 'translate3d(0, 0, 0) rotate(0deg)';
    card.style.setProperty('--drag-progress', '0');

    try {
      card.setPointerCapture(state.pointerId);
      state.captured = true;
    } catch {
      // Capture is best effort; the drag still works while over the card.
      state.captured = false;
    }
  }

  function updateDrag(state, dx, dy) {
    const { card, thresholds } = state;

    const horizontal = Math.min(1, Math.abs(dx) / thresholds.x);
    const vertical = dy < 0 ? Math.min(1, -dy / thresholds.y) : 0;
    const progress = Math.max(horizontal, vertical);

    card.style.transform =
      `translate3d(${round(dx)}px, ${round(dy)}px, 0) ` +
      `rotate(${round(dx / ROTATION_DEG)}deg)`;
    card.style.setProperty('--drag-progress', progress.toFixed(3));

    setDragIntent(state, intentFrom(dx, dy));
  }

  function intentFrom(dx, dy) {
    if (Math.abs(dx) < INTENT_MIN_PX && Math.abs(dy) < INTENT_MIN_PX) return null;
    if (Math.abs(dx) >= Math.abs(dy)) return dx > 0 ? 'yes' : 'no';
    return dy < 0 ? 'maybe' : null;
  }

  function setDragIntent(state, decision) {
    if (state.intent === decision) return;

    const { card } = state;
    card.classList.remove(DRAG_CLASS.no, DRAG_CLASS.maybe, DRAG_CLASS.yes);
    if (decision) {
      card.classList.add(DRAG_CLASS[decision]);
      card.dataset.decision = decision;
    } else {
      delete card.dataset.decision;
    }
    state.intent = decision;
  }

  function decideFromDrag(dx, dy, thresholds) {
    let horizontal = null;
    if (dx >= thresholds.x) horizontal = 'yes';
    else if (dx <= -thresholds.x) horizontal = 'no';

    const vertical = dy <= -thresholds.y ? 'maybe' : null;
    if (horizontal && vertical) {
      // Both thresholds crossed: the dominant axis wins.
      return Math.abs(dy) > Math.abs(dx) ? 'maybe' : horizontal;
    }
    return horizontal || vertical;
  }

  function releasePointerCapture(state) {
    if (!state.captured) return;
    try {
      state.card.releasePointerCapture(state.pointerId);
    } catch {
      // Already released by the browser, for example after pointercancel.
    }
    state.captured = false;
  }

  function armClickGuard() {
    suppressNextClick = true;
    window.clearTimeout(clickGuardTimer);
    clickGuardTimer = window.setTimeout(() => {
      suppressNextClick = false;
    }, CLICK_GUARD_MS);
  }

  function onDocumentClick(event) {
    if (!suppressNextClick || !event.isTrusted) return;

    // A drag must not also activate a button or follow the photo link.
    suppressNextClick = false;
    window.clearTimeout(clickGuardTimer);
    event.preventDefault();
    event.stopPropagation();
  }

  // ---------------------------------------------------------------- resolve

  function snapBack(card) {
    if (!card || !card.isConnected) return;

    cancelSnapBack(card);
    clearDragFeedback(card);
    card.classList.add(CLASS_SNAP_BACK);
    card.style.transform = 'translate3d(0, 0, 0) rotate(0deg)';
    card.style.setProperty('--drag-progress', '0');

    let finished = false;
    let timer = 0;

    const finish = () => {
      if (finished) return;
      finished = true;
      window.clearTimeout(timer);
      card.removeEventListener('transitionend', onEnd);
      card.classList.remove(CLASS_SNAP_BACK);
      if (snapBacks.get(card) === finish) snapBacks.delete(card);
    };

    const onEnd = (event) => {
      if (event.target !== card) return;
      if (event.propertyName && event.propertyName !== 'transform') return;
      finish();
    };

    card.addEventListener('transitionend', onEnd);
    timer = window.setTimeout(finish, SNAP_BACK_TIMEOUT_MS);
    snapBacks.set(card, finish);
  }

  function cancelSnapBack(card) {
    const finish = snapBacks.get(card);
    if (finish) finish();
  }

  function clearDragFeedback(card) {
    card.classList.remove(
      CLASS_DRAGGING,
      DRAG_CLASS.no,
      DRAG_CLASS.maybe,
      DRAG_CLASS.yes,
    );
    delete card.dataset.decision;
  }

  function commitDecision(card, decision) {
    if (!card || !card.isConnected) return;
    if (!card.classList.contains(CLASS_ACTIVE)) return;
    if (card.classList.contains(CLASS_FLYING)) return;

    const flyClass = FLY_CLASS[decision];
    const button = card.querySelector(`[data-decision="${decision}"]`);
    if (!flyClass || !button) return;

    cancelSnapBack(card);
    clearDragFeedback(card);

    // The fly class owns the final transform; an inline drag transform would
    // otherwise keep overriding it.
    card.style.removeProperty('transform');
    card.style.setProperty('--drag-progress', '1');
    card.classList.add(CLASS_FLYING, flyClass);

    vibrate();
    button.click(); // HTMX performs the POST; this file never uses fetch.

    if (prefersReducedMotion()) {
      finishCard(card);
    } else {
      window.setTimeout(() => finishCard(card), FLY_OUT_MS);
    }
  }

  function finishCard(card) {
    if (!card) return;

    cancelSnapBack(card);
    card.remove();
    promoteNextCard();
  }

  function promoteNextCard() {
    if (!deck || !deck.isConnected) return;

    const next = deck.querySelector(CARD_SELECTOR);
    if (!next) {
      if (deck.childElementCount === 0) deck.classList.add(CLASS_EMPTY);
      return;
    }

    next.classList.add(CLASS_ACTIVE);
    deck.classList.remove(CLASS_EMPTY);
    updateProgress(next);
    prefetchNextPhoto();
  }

  function updateProgress(card) {
    const target = document.getElementById(PROGRESS_ID);
    const remaining = card && card.dataset.remaining;
    if (!target || typeof remaining !== 'string' || remaining === '') return;
    target.textContent = remaining;
  }

  function prefetchNextPhoto() {
    if (!deck) return;

    const second = deck.querySelectorAll(CARD_SELECTOR)[1];
    if (!second) return;

    const photo = second.querySelector(PHOTO_SELECTOR);
    const src = photo && photo.src;
    if (!src) return;
    if (prefetchedImage && prefetchedImage.src === src) return;

    const image = new Image();
    image.decoding = 'async';
    image.src = src;
    prefetchedImage = image; // keep a reference so the request is not dropped
  }

  // --------------------------------------------------------------- keyboard

  function onKeyDown(event) {
    if (!deck || !deck.isConnected) return;
    if (event.defaultPrevented || event.isComposing || event.repeat) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (isTypingTarget(event.target)) return;

    switch (event.key) {
      case 'ArrowLeft':
      case 'ArrowRight':
      case 'ArrowUp':
        event.preventDefault();
        decideActive(KEY_DECISION[event.key]);
        return;
      case 'z':
      case 'Z':
        clickUndo(event);
        return;
      case 'o':
      case 'O':
        openActive(event);
        return;
      default:
    }
  }

  function isTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    if (target.isContentEditable) return true;

    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  function decideActive(decision) {
    const card = activeCard();
    if (card) commitDecision(card, decision);
  }

  function clickUndo(event) {
    const toast = document.getElementById(TOAST_ID);
    if (!toast) return;

    const undo = toast.querySelector('.undo') || toast.querySelector('button');
    if (!undo || typeof undo.click !== 'function') return;

    event.preventDefault();
    undo.click();
  }

  function openActive(event) {
    const card = activeCard();
    const link = card && card.querySelector(OPEN_SELECTOR);
    if (!link || !link.href) return;

    event.preventDefault();
    window.open(link.href, '_blank', 'noopener');
  }

  function activeCard() {
    return deck
      ? deck.querySelector(`${CARD_SELECTOR}.${CLASS_ACTIVE}:not(.${CLASS_FLYING})`)
      : null;
  }

  // ------------------------------------------------------------ click guard

  function onDeckClick(event) {
    // Programmatic clicks come from commitDecision and must reach HTMX as-is.
    if (!event.isTrusted) return;

    const target = event.target instanceof Element ? event.target : null;
    const card = target ? target.closest(CARD_SELECTOR) : null;
    if (!card || !deck || !deck.contains(card)) return;

    // A card that is leaving, or sits behind the top card, ignores input.
    if (!card.classList.contains(CLASS_ACTIVE) || card.classList.contains(CLASS_FLYING)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    const button = target.closest(DECISION_BUTTON_SELECTOR);
    if (!button || !card.contains(button)) return;

    // Replace the native activation with the animated commit path so a tap
    // removes the card exactly like a swipe and posts exactly once.
    event.preventDefault();
    event.stopPropagation();
    commitDecision(card, button.dataset.decision);
  }

  // --------------------------------------------------------------- utilities

  function vibrate() {
    if (typeof navigator === 'undefined') return;
    if (typeof navigator.vibrate !== 'function') return;
    navigator.vibrate(10);
  }

  function prefersReducedMotion() {
    return typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function round(value) {
    return Math.round(value * 100) / 100;
  }
})();

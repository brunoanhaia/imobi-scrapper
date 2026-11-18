/*
 * Gesture translator for the deck. It is NOT responsible for saving anything:
 * swipe and arrow keys only call form.requestSubmit(), so the browser does the
 * POST and the server renders the next card. If this file fails to load, the
 * buttons still work (plain form).
 */
(() => {
  'use strict';

  var deck = document.getElementById('deck');
  if (!deck) return;

  var HORIZONTAL = 0.35; // fraction of the card width
  var VERTICAL = 0.25; // fraction of the card height
  var drag = null;
  var suppressClickUntil = 0;

  function activeCard() {
    return deck.querySelector('form.deck-card.is-active');
  }

  function submit(card, decision) {
    var button = card?.querySelector('[data-decision="' + decision + '"]');
    if (!button) return;
    if (typeof card.requestSubmit === 'function') card.requestSubmit(button);
    else button.click();
  }

  function clear(card) {
    card.classList.remove('dragging', 'drag-left', 'drag-right', 'drag-up');
    card.style.transform = '';
  }

  // The photo is inside a link; without this the browser starts a native drag and
  // cancels the pointer sequence.
  deck.addEventListener('dragstart', (event) => {
    event.preventDefault();
  });

  deck.addEventListener('pointerdown', (event) => {
    var card = event.target.closest('form.deck-card.is-active');
    if (!card) return;
    // The action bar keeps native behaviour; the rest of the card (including the photo
    // link) is the drag surface, so a swipe over the image still works.
    if (event.target.closest('.deck-actions')) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;

    drag = {
      card: card,
      x: event.clientX,
      y: event.clientY,
      id: event.pointerId,
      moved: false,
    };
    card.classList.add('dragging');
    card.setPointerCapture?.(event.pointerId);
  });

  deck.addEventListener('pointermove', (event) => {
    if (event.pointerId !== drag?.id) return;

    var dx = event.clientX - drag.x;
    var dy = event.clientY - drag.y;
    var width = drag.card.offsetWidth || 1;
    var height = drag.card.offsetHeight || 1;

    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) drag.moved = true;

    drag.card.style.transform =
      'translate3d(' + dx + 'px, ' + dy + 'px, 0) rotate(' + dx / 20 + 'deg)';
    drag.card.classList.toggle('drag-right', dx > width * 0.12);
    drag.card.classList.toggle('drag-left', dx < -width * 0.12);
    drag.card.classList.toggle('drag-up', dy < -height * VERTICAL * 0.6);
  });

  deck.addEventListener('pointerup', (event) => {
    if (event.pointerId !== drag?.id) return;

    var card = drag.card;
    var dx = event.clientX - drag.x;
    var dy = event.clientY - drag.y;
    var moved = drag.moved;
    drag = null;
    finishDrag(card, dx, dy, moved);
  });

  function finishDrag(card, dx, dy, moved) {
    // A drag over the photo link must not open the listing afterwards.
    if (moved) {
      suppressClickUntil = Date.now() + 400;
      card.classList.remove('dragging');
    }

    var decision = decisionFor(card, dx, dy);
    if (!decision) {
      clear(card);
      return;
    }
    fly(card, decision);
    submit(card, decision);
  }

  function decisionFor(card, dx, dy) {
    var width = card.offsetWidth || 1;
    var height = card.offsetHeight || 1;
    if (Math.abs(dx) > width * HORIZONTAL) return dx > 0 ? 'yes' : 'no';
    if (dy < -height * VERTICAL) return 'maybe';
    return null;
  }

  function fly(card, decision) {
    card.classList.add('flying');
    var classes = { yes: 'fly-right', no: 'fly-left', maybe: 'fly-up' };
    card.classList.add(classes[decision]);
  }

  document.addEventListener(
    'click',
    (event) => {
      if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  deck.addEventListener('pointercancel', () => {
    if (drag) clear(drag.card);
    drag = null;
  });

  // Tap/click anywhere on the card (except the action bar) opens the listing page.
  deck.addEventListener('click', (event) => {
    if (Date.now() < suppressClickUntil) return;
    var card = event.target.closest('form.deck-card.is-active');
    if (!card?.dataset.href) return;
    if (event.target.closest('a, button, .deck-actions')) return;
    var href = card.dataset.href;
    if (href.startsWith('/') && !href.startsWith('//')) window.location.assign(href);
  });

  document.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;

    if (event.key === 'Enter') {
      openExternal(event);
      return;
    }

    if (event.target?.closest?.('input, textarea, select, [contenteditable]')) {
      return;
    }

    var decision = decisionForKey(event);
    if (!decision) return;

    var card = activeCard();
    if (!card) return;

    event.preventDefault();
    submit(card, decision);
  });

  // Enter opens the original ad (the agency/portal page) in a new tab;
  // inputs, buttons and links keep their native behaviour.
  function openExternal(event) {
    if (event.target?.closest?.('input, textarea, select, button, a, [contenteditable]')) {
      return;
    }
    var card = activeCard();
    if (!card?.dataset.external) return;
    event.preventDefault();
    window.open(card.dataset.external, '_blank', 'noopener');
  }

  // ArrowUp alone must scroll the page; only Shift+ArrowUp decides "maybe".
  function decisionForKey(event) {
    if (event.key === 'ArrowUp' && event.shiftKey) return 'maybe';
    var map = { ArrowLeft: 'no', ArrowRight: 'yes' };
    return map[event.key] || null;
  }
})();

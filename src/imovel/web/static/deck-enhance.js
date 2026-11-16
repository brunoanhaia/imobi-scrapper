/*
 * Gesture translator for the deck. It is NOT responsible for saving anything:
 * swipe and arrow keys only call form.requestSubmit(), so the browser does the
 * POST and the server renders the next card. If this file fails to load, the
 * buttons still work (plain form).
 */
(function () {
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
    var button = card && card.querySelector('[data-decision="' + decision + '"]');
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
  deck.addEventListener('dragstart', function (event) {
    event.preventDefault();
  });

  deck.addEventListener('pointerdown', function (event) {
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
    if (card.setPointerCapture) card.setPointerCapture(event.pointerId);
  });

  deck.addEventListener('pointermove', function (event) {
    if (!drag || event.pointerId !== drag.id) return;

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

  deck.addEventListener('pointerup', function (event) {
    if (!drag || event.pointerId !== drag.id) return;

    var card = drag.card;
    var dx = event.clientX - drag.x;
    var dy = event.clientY - drag.y;
    var width = card.offsetWidth || 1;
    var height = card.offsetHeight || 1;
    var moved = drag.moved;
    drag = null;

    // A drag over the photo link must not open the listing afterwards.
    if (moved) {
      suppressClickUntil = Date.now() + 400;
      card.classList.remove('dragging');
    }

    var decision = null;
    if (Math.abs(dx) > width * HORIZONTAL) decision = dx > 0 ? 'yes' : 'no';
    else if (dy < -height * VERTICAL) decision = 'maybe';

    if (!decision) {
      clear(card);
      return;
    }

    card.classList.add('flying');
    if (decision === 'yes') card.classList.add('fly-right');
    if (decision === 'no') card.classList.add('fly-left');
    if (decision === 'maybe') card.classList.add('fly-up');
    submit(card, decision);
  });

  document.addEventListener(
    'click',
    function (event) {
      if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  deck.addEventListener('pointercancel', function () {
    if (drag) clear(drag.card);
    drag = null;
  });

  // Tap/click anywhere on the card (except the action bar) opens the listing page.
  deck.addEventListener('click', function (event) {
    if (Date.now() < suppressClickUntil) return;
    var card = event.target.closest('form.deck-card.is-active');
    if (!card || !card.dataset.href) return;
    if (event.target.closest('a, button, .deck-actions')) return;
    window.location.href = card.dataset.href;
  });

  document.addEventListener('keydown', function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;

    // Enter opens the original ad (the agency/portal page) in a new tab;
    // buttons and links keep their native behaviour.
    if (event.key === 'Enter') {
      var target = event.target;
      if (target && target.closest && target.closest('input, textarea, select, button, a, [contenteditable]')) {
        return;
      }
      var current = activeCard();
      if (!current || !current.dataset.external) return;
      event.preventDefault();
      window.open(current.dataset.external, '_blank', 'noopener');
      return;
    }

    var target = event.target;
    if (target && target.closest && target.closest('input, textarea, select, [contenteditable]')) {
      return;
    }

    var map = { ArrowLeft: 'no', ArrowUp: 'maybe', ArrowRight: 'yes' };
    var decision = map[event.key];
    if (!decision) return;

    var card = activeCard();
    if (!card) return;

    event.preventDefault();
    submit(card, decision);
  });
})();

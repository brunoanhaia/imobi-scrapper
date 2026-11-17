/*
 * Small resilience layer for an unstable connection.
 *
 * - tells the user when the browser is offline;
 * - a triage POST that fails is retried once and then stored in localStorage;
 *   the queue is flushed when the connection comes back;
 * - keeps the scroll position when going back from a listing to the list.
 */
(() => {
  'use strict';

  var QUEUE_KEY = 'imovel.triage.queue';
  var RETRY_DELAY = 1500;

  function readQueue() {
    try {
      var raw = window.localStorage.getItem(QUEUE_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function writeQueue(items) {
    try {
      window.localStorage.setItem(QUEUE_KEY, JSON.stringify(items));
    } catch (error) {
      /* storage full or disabled: nothing else to do */
    }
  }

  function enqueue(item) {
    var queue = readQueue();
    queue.push(item);
    writeQueue(queue);
  }

  function toast(message) {
    var element = document.getElementById('toast');
    if (!element) return;
    element.textContent = message;
    element.classList.add('show', 'offline');
    window.setTimeout(() => {
      element.classList.remove('show', 'offline');
    }, 6000);
  }

  function setOnlineState(online) {
    document.documentElement.classList.toggle('is-offline', !online);
    var banner = document.getElementById('offline-banner');
    if (banner) banner.hidden = online;
  }

  function flushQueue() {
    var queue = readQueue();
    if (!queue.length) return;

    setOnlineState(true);
    var remaining = [];
    var sent = 0;

    queue.forEach((item) => {
      var body = new URLSearchParams({ decision: item.decision, mode: 'queue' });
      window
        .fetch('/triagem/' + item.id, {
          method: 'POST',
          body: body,
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        })
        .then((response) => {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          sent += 1;
          var card = document.getElementById('card-' + item.id);
          if (card) card.remove();
        })
        .catch(() => {
          remaining.push(item);
        })
        .finally(() => {
          writeQueue(remaining);
          if (sent > 0) toast(sent + ' decisão(ões) enviada(s)');
        });
    });
  }

  function retryTriage(event) {
    var elt = event && event.detail ? event.detail.elt : null;
    if (!elt || !elt.getAttribute) return;

    var decision = elt.getAttribute('data-decision');
    var id = elt.getAttribute('data-listing-id');
    if (!decision || !id) return;

    enqueue({ id: id, decision: decision, at: Date.now() });
    toast('Sem conexão — decisão guardada, envio quando voltar');
  }

  document.addEventListener('htmx:sendError', retryTriage);
  document.addEventListener('htmx:responseError', retryTriage);
  document.addEventListener('htmx:afterRequest', (event) => {
    var elt = event.detail.elt;
    if (elt && elt.getAttribute && elt.getAttribute('data-decision')) {
      elt.classList.add('is-sent');
    }
  });

  // Tapping the card (not a button or link) opens the listing page.
  document.addEventListener('click', (event) => {
    var card = event.target.closest('[data-href]');
    if (!card) return;
    if (event.target.closest('a, button, form.deck-actions')) return;
    window.location.href = card.getAttribute('data-href');
  });

  window.addEventListener('online', flushQueue);
  window.addEventListener('offline', () => {
    setOnlineState(false);
  });

  document.addEventListener('DOMContentLoaded', () => {
    setOnlineState(navigator.onLine !== false);
    flushQueue();
    if ('scrollRestoration' in window.history) {
      window.history.scrollRestoration = 'auto';
    }
  });
})();

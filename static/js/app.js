/**
 * Hardware RAG Agent — Frontend Application
 * HTMX + SSE streaming chat interface
 */

(function () {
  'use strict';

  // ─── state ────────────────────────────────────────────────
  const state = {
    settings: {
      apiKey: '',
      baseUrl: '',
      model: '',
    },
    streaming: false,
    conversationId: null,
    currentAiMessage: null, // { bubble, sourcesContainer }
    models: [],
  };

  // ─── DOM refs ─────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const chatContainer = $('#chat-container');
  const messageInput = $('#message-input');
  const sendBtn = $('#btn-send');
  const settingsOverlay = $('#settings-overlay');
  const settingsPanel = $('#settings-panel');
  const toastEl = $('#toast');

  // settings form refs
  const apiKeyInput = $('#api-key');
  const baseUrlInput = $('#base-url');
  const modelSelect = $('#model-select');
  const fetchModelsBtn = $('#fetch-models');
  const modelFetchStatus = $('#model-fetch-status');
  const saveSettingsBtn = $('#save-settings');

  // ─── init ─────────────────────────────────────────────────
  function init() {
    loadSettings();
    bindEvents();
    renderWelcome();
  }

  // ─── settings (localStorage) ─────────────────────────────
  function loadSettings() {
    try {
      const saved = localStorage.getItem('hardware-rag-settings');
      if (saved) {
        const parsed = JSON.parse(saved);
        state.settings.apiKey = parsed.apiKey || '';
        state.settings.baseUrl = parsed.baseUrl || '';
        state.settings.model = parsed.model || '';
        if (apiKeyInput) apiKeyInput.value = state.settings.apiKey;
        if (baseUrlInput) baseUrlInput.value = state.settings.baseUrl;
        if (modelSelect) {
          const models = parsed.models || [];
          state.models = models;
          populateModelSelect(models);
          modelSelect.value = state.settings.model;
        }
      }
    } catch (e) {
      console.warn('Failed to load settings:', e);
    }
  }

  function saveSettingsToStorage() {
    try {
      const data = {
        apiKey: state.settings.apiKey,
        baseUrl: state.settings.baseUrl,
        model: state.settings.model,
        models: state.models,
      };
      localStorage.setItem('hardware-rag-settings', JSON.stringify(data));
    } catch (e) {
      console.warn('Failed to save settings:', e);
    }
  }

  // ─── model select ─────────────────────────────────────────
  function populateModelSelect(models) {
    if (!modelSelect) return;
    modelSelect.innerHTML = '<option value="">— 请先拉取模型列表 —</option>';

    if (models.length === 0) return;

    models.forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m.id || m;
      opt.textContent = m.id || m;
      modelSelect.appendChild(opt);
    });
  }

  async function fetchModels() {
    const baseUrl = (baseUrlInput.value || '').trim();
    const apiKey = (apiKeyInput.value || '').trim();

    if (!baseUrl) {
      setModelFetchStatus('请先填写 Base URL', 'error');
      return;
    }
    if (!apiKey) {
      setModelFetchStatus('请先填写 API Key', 'error');
      return;
    }

    setModelFetchStatus('正在拉取模型列表…', 'loading');
    fetchModelsBtn.disabled = true;

    try {
      const url = baseUrl.replace(/\/+$/, '') + '/models';
      const resp = await fetch(url, {
        headers: {
          'Authorization': 'Bearer ' + apiKey,
          'Accept': 'application/json',
        },
      });

      if (!resp.ok) {
        throw new Error('HTTP ' + resp.status + ': ' + (await resp.text()).slice(0, 100));
      }

      const data = await resp.json();
      let models = data.data || data;

      if (!Array.isArray(models)) {
        models = Object.values(models);
      }

      models = models.map((m) => (typeof m === 'string' ? { id: m } : m)).filter((m) => m && m.id);

      if (models.length === 0) {
        throw new Error('未找到可用模型');
      }

      state.models = models;
      populateModelSelect(models);
      setModelFetchStatus('已加载 ' + models.length + ' 个模型', 'success');
    } catch (err) {
      setModelFetchStatus('拉取失败: ' + err.message, 'error');
    } finally {
      fetchModelsBtn.disabled = false;
    }
  }

  function setModelFetchStatus(msg, type) {
    if (!modelFetchStatus) return;
    modelFetchStatus.textContent = msg;
    modelFetchStatus.className = 'model-fetch-status' + (type ? ' ' + type : '');
  }

  // ─── apply settings ───────────────────────────────────────
  function applySettings() {
    state.settings.apiKey = (apiKeyInput.value || '').trim();
    state.settings.baseUrl = (baseUrlInput.value || '').trim();
    state.settings.model = modelSelect ? modelSelect.value : '';
    saveSettingsToStorage();
    closeSettings();
    showToast('设置已保存');
  }

  // ─── settings panel ──────────────────────────────────────
  function openSettings() {
    apiKeyInput.value = state.settings.apiKey;
    baseUrlInput.value = state.settings.baseUrl;
    populateModelSelect(state.models);
    if (modelSelect) modelSelect.value = state.settings.model;
    settingsOverlay.classList.add('open');
    settingsPanel.classList.add('open');
  }

  function closeSettings() {
    settingsOverlay.classList.remove('open');
    settingsPanel.classList.remove('open');
  }

  // ─── toast ────────────────────────────────────────────────
  let toastTimer = null;

  function showToast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3000);
  }

  // ─── welcome screen ──────────────────────────────────────
  function renderWelcome() {
    chatContainer.innerHTML = '';
    const welcome = document.createElement('div');
    welcome.className = 'welcome-screen';
    welcome.innerHTML =
      '<div class="welcome-icon">&#9889;</div>' +
      '<h2>Hardware RAG Agent</h2>' +
      '<p>&#22522;&#20110;&#23448;&#26041;&#30828;&#20214;&#25991;&#26723;&#30340;&#26234;&#33021;&#38382;&#31572;&#21161;&#25163;&#12290;<br>&#20808;&#28857;&#20987;&#21491;&#19978;&#35282; &#9881;&#65039; &#37197;&#32622; API Key &#21644;&#27169;&#22411;&#12290;</p>' +
      '<div class="welcome-hints">' +
        '<span class="welcome-hint" data-hint="ESP32 &#30340; ADC &#24341;&#33050;&#30005;&#21387;&#33539;&#22260;&#26159;&#22810;&#23569;&#65311;">ESP32 ADC &#30005;&#21387;&#33539;&#22260;</span>' +
        '<span class="welcome-hint" data-hint="I2C &#21644; SPI &#26377;&#20160;&#20040;&#21306;&#21035;&#65311;">I2C vs SPI</span>' +
        '<span class="welcome-hint" data-hint="&#20889;&#19968;&#27573; STM32 GPIO &#21021;&#22987;&#21270;&#20195;&#30701;">STM32 GPIO &#20195;&#30701;</span>' +
        '<span class="welcome-hint" data-hint="&#26641;&#33945;&#27966; 5 &#21644; Jetson Orin &#24590;&#20040;&#36873;&#65311;">&#26641;&#33945;&#27966; vs Jetson</span>' +
      '</div>';
    chatContainer.appendChild(welcome);
    appendScrollAnchor();
  }

  // ─── message rendering ──────────────────────────────────
  function addUserMessage(text) {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message user';
    div.innerHTML =
      '<div class="bubble">' + escapeHtml(text) + '</div>' +
      '<span class="timestamp">' + formatTime(new Date()) + '</span>';
    chatContainer.appendChild(div);
    scrollToBottom();
    return div;
  }

  function addAiMessage() {
    removeWelcome();
    // Create a container for the AI message including sources
    const wrapper = document.createElement('div');
    wrapper.className = 'message ai';
    wrapper.innerHTML =
      '<div class="bubble stream-cursor" id="ai-bubble"></div>' +
      '<div class="ai-sources"></div>' +
      '<span class="timestamp">' + formatTime(new Date()) + '</span>';
    chatContainer.appendChild(wrapper);

    state.currentAiMessage = {
      bubble: wrapper.querySelector('.bubble'),
      sourcesContainer: wrapper.querySelector('.ai-sources'),
      wrapper: wrapper,
    };
    scrollToBottom();
    return wrapper;
  }

  function renderSources(sources) {
    if (!sources || sources.length === 0 || !state.currentAiMessage) return;

    const container = state.currentAiMessage.sourcesContainer;
    if (!container) return;

    const details = document.createElement('details');
    details.className = 'sources-toggle';
    let itemsHtml = '';
    sources.forEach((s) => {
      const name = s.title || s.filename || s.source || '未知';
      const score = s.score != null ? s.score : s.relevance_score;
      const scoreStr = score != null ? Number(score).toFixed(2) : '—';
      itemsHtml +=
        '<div class="source-item">' +
          '<span class="source-name">&#128196; ' + escapeHtml(name) + '</span>' +
          '<span class="source-score">' + scoreStr + '</span>' +
        '</div>';
    });

    details.innerHTML =
      '<summary>&#128269; &#21442;&#32771;&#26469;&#28304; (' + sources.length + ')</summary>' +
      '<div class="sources-list">' + itemsHtml + '</div>';

    container.appendChild(details);
    scrollToBottom();
  }

  // ─── SSE streaming ──────────────────────────────────────
  function startStream() {
    if (state.streaming) return;

    const msgText = messageInput.value.trim();
    if (!msgText) return;

    // Check settings
    if (!state.settings.apiKey || !state.settings.baseUrl) {
      showToast('请先在设置中配置 API Key 和 Base URL');
      openSettings();
      return;
    }

    state.streaming = true;
    messageInput.disabled = true;
    sendBtn.disabled = true;

    addUserMessage(msgText);
    messageInput.value = '';
    messageInput.style.height = 'auto';

    addAiMessage();
    const bubble = state.currentAiMessage.bubble;
    let buffer = '';

    const baseUrl = state.settings.baseUrl.replace(/\/+$/, '');
    const apiKey = state.settings.apiKey;
    const model = state.settings.model;

    const xhr = new XMLHttpRequest();
    xhr.open('POST', baseUrl + '/chat/stream', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.setRequestHeader('Accept', 'text/event-stream');
    if (apiKey) xhr.setRequestHeader('X-API-Key', apiKey);
    if (model) xhr.setRequestHeader('X-Model', model);
    if (state.conversationId) xhr.setRequestHeader('X-Conversation-Id', state.conversationId);

    let lastIndex = 0;
    let sources = null;

    xhr.onprogress = function () {
      const newData = xhr.responseText.slice(lastIndex);
      lastIndex = xhr.responseText.length;

      const lines = newData.split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;

        const payload = trimmed.slice(5).trim();

        if (payload === '[DONE]') continue;

        try {
          const parsed = JSON.parse(payload);

          // conversation_id from first message
          if (parsed.conversation_id) {
            state.conversationId = parsed.conversation_id;
          }

          // SSE content chunk format: { content: "..." }
          if (parsed.content) {
            buffer += parsed.content;
          }

          // OpenAI-compatible delta: { choices: [{ delta: { content: "..." } }] }
          if (parsed.choices && parsed.choices[0]) {
            const delta = parsed.choices[0].delta;
            if (delta && delta.content) {
              buffer += delta.content;
            }
            // finished
            if (parsed.choices[0].finish_reason) {
              sources = parsed.sources || (parsed.metadata && parsed.metadata.sources) || sources;
            }
          }

          // sources
          if (parsed.sources) sources = parsed.sources;
          if (parsed.metadata && parsed.metadata.sources) sources = parsed.metadata.sources;

          // update bubble
          if (bubble && buffer) {
            bubble.textContent = buffer;
            applyCodeHighlighting(bubble);
            scrollToBottom();
          }
        } catch (e) {
          // partial chunk parse errors are normal
        }
      }
    };

    xhr.onloadend = function () {
      state.streaming = false;
      messageInput.disabled = false;
      sendBtn.disabled = false;
      messageInput.focus();

      if (bubble) {
        bubble.classList.remove('stream-cursor');
      }

      // render sources after stream ends
      if (sources && sources.length > 0) {
        renderSources(sources);
      }

      // error handling
      if (xhr.status >= 400) {
        let errMsg = '请求失败 (HTTP ' + xhr.status + ')';
        try {
          const errBody = JSON.parse(xhr.responseText);
          if (errBody.detail) errMsg += ': ' + errBody.detail;
        } catch (_) {}
        if (bubble) {
          bubble.textContent = '&#9888;&#65039; ' + errMsg + '。请检查 API Key 和 Base URL 是否正确。';
        }
      }

      scrollToBottom();
    };

    const body = JSON.stringify({
      message: msgText,
      conversation_id: state.conversationId,
    });
    xhr.send(body);
  }

  // ─── helpers ─────────────────────────────────────────────
  function removeWelcome() {
    const welcome = chatContainer.querySelector('.welcome-screen');
    if (welcome) welcome.remove();
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      const anchor = document.getElementById('scroll-anchor');
      if (anchor) anchor.scrollIntoView({ behavior: 'smooth' });
    });
  }

  function appendScrollAnchor() {
    if (!document.getElementById('scroll-anchor')) {
      const anchor = document.createElement('div');
      anchor.id = 'scroll-anchor';
      chatContainer.appendChild(anchor);
    }
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatTime(date) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }

  // ─── code highlighting ─────────────────────────────────
  function applyCodeHighlighting(container) {
    if (window.hljs) {
      container.querySelectorAll('pre code').forEach(function (block) {
        if (!block.dataset.highlighted) {
          hljs.highlightElement(block);
          block.dataset.highlighted = 'true';
        }
      });
    }
  }

  function attachCopyButtons(root) {
    (root || document).querySelectorAll('pre').forEach(function (pre) {
      if (pre.parentNode && pre.parentNode.querySelector('.code-header')) return;

      var code = pre.querySelector('code');
      if (!code) return;

      var lang = (code.className || '').replace(/^language-/, '') || 'code';

      var header = document.createElement('div');
      header.className = 'code-header';
      header.innerHTML = '<span>' + escapeHtml(lang) + '</span>' +
        '<button class="btn-copy" data-copy>&#22797;&#21046;</button>';

      pre.parentNode.insertBefore(header, pre);

      var copyBtn = header.querySelector('.btn-copy');
      copyBtn.addEventListener('click', function () {
        var text = code.textContent || '';
        navigator.clipboard.writeText(text).then(function () {
          copyBtn.textContent = '&#24050;&#22797;&#21046;';
          copyBtn.classList.add('copied');
          setTimeout(function () {
            copyBtn.textContent = '&#22797;&#21046;';
            copyBtn.classList.remove('copied');
          }, 2000);
        }).catch(function () {
          var ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          copyBtn.textContent = '&#24050;&#22797;&#21046;';
          copyBtn.classList.add('copied');
          setTimeout(function () {
            copyBtn.textContent = '&#22797;&#21046;';
            copyBtn.classList.remove('copied');
          }, 2000);
        });
      });
    });
  }

  // ─── event binding ──────────────────────────────────────
  function bindEvents() {
    // settings toggle
    $('#btn-settings').addEventListener('click', openSettings);
    $('#btn-close-settings').addEventListener('click', closeSettings);
    settingsOverlay.addEventListener('click', function (e) {
      if (e.target === settingsOverlay) closeSettings();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && settingsPanel.classList.contains('open')) {
        closeSettings();
      }
    });

    // fetch models
    fetchModelsBtn.addEventListener('click', fetchModels);

    // save settings
    saveSettingsBtn.addEventListener('click', applySettings);

    // send message
    sendBtn.addEventListener('click', startStream);

    // Enter to send, Shift+Enter for newline
    messageInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        startStream();
      }
    });

    // auto-resize textarea
    messageInput.addEventListener('input', function () {
      messageInput.style.height = 'auto';
      messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
    });

    // welcome hint click
    chatContainer.addEventListener('click', function (e) {
      var hint = e.target.closest('.welcome-hint');
      if (hint) {
        messageInput.value = hint.dataset.hint;
        messageInput.focus();
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
      }
    });

    // mutation observer for dynamic copy buttons
    var copyObserver = new MutationObserver(function () {
      attachCopyButtons();
    });
    copyObserver.observe(chatContainer, { childList: true, subtree: true });
    attachCopyButtons();
  }

  // ─── startup ────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();

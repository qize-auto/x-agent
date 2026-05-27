const vscode = acquireVsCodeApi();

const statusBar = document.getElementById('status-bar');
const chatHistory = document.getElementById('chat-history');
const inputBox = document.getElementById('input-box');
const sendBtn = document.getElementById('send-btn');

function setStatus(text, ok) {
  statusBar.textContent = text;
  statusBar.className = ok ? 'connected' : 'disconnected';
}

function appendMessage(text, sender) {
  const div = document.createElement('div');
  div.className = 'msg ' + sender;
  const label = sender === 'user' ? 'You' : (sender === 'error' ? 'Error' : 'Agent');
  div.innerHTML = '<div class="label">' + label + '</div>' + escapeHtml(text);
  chatHistory.appendChild(div);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function appendTaskPlan(goal, subtasks) {
  const div = document.createElement('div');
  div.className = 'msg agent';
  let html = '<div class="label">Task Plan</div><div class="task-plan">';
  html += '<strong>' + escapeHtml(goal) + '</strong><ul>';
  subtasks.forEach(st => {
    const cls = st.status === 'done' || st.status === 'skipped' ? 'done' : '';
    html += '<li class="' + cls + '">[' + st.status + '] ' + escapeHtml(st.description) + '</li>';
  });
  html += '</ul></div>';
  div.innerHTML = html;
  chatHistory.appendChild(div);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function appendCodeBlock(code, lang) {
  const div = document.createElement('div');
  div.className = 'msg agent';
  div.innerHTML = '<div class="label">Code</div><pre class="code-block">' + escapeHtml(code) + '</pre>' +
    '<button class="insert-btn" data-action="insert">Insert at Cursor</button>' +
    '<button class="insert-btn" data-action="apply" style="margin-left:6px;">Apply to Current File</button>';
  div.querySelector('[data-action="insert"]').addEventListener('click', () => {
    vscode.postMessage({ type: 'insertCode', code });
  });
  div.querySelector('[data-action="apply"]').addEventListener('click', () => {
    // 尝试从代码块中提取 SEARCH/REPLACE
    const searchMatch = code.match(/<<<<<<< SEARCH\n([\s\S]*?)\n=======\n([\s\S]*?)\n>>>>>>> REPLACE/);
    if (searchMatch) {
      vscode.postMessage({ type: 'applyEdit', search: searchMatch[1], replace: searchMatch[2] });
    } else {
      // 如果不是 SEARCH/REPLACE 格式，尝试将整个代码块作为 replace，让用户选择 search
      vscode.postMessage({ type: 'applyEdit', search: '', replace: code });
    }
  });
  chatHistory.appendChild(div);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

sendBtn.addEventListener('click', () => {
  const text = inputBox.value.trim();
  if (!text) return;
  appendMessage(text, 'user');
  inputBox.value = '';
  sendBtn.disabled = true;

  const isTask = text.length > 100 || /^(plan|task|帮我|搭建|重构)/i.test(text);
  vscode.postMessage({ type: isTask ? 'task' : 'chat', text });
});

inputBox.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});

window.addEventListener('message', (event) => {
  const msg = event.data;
  switch (msg.type) {
    case 'status':
      setStatus(msg.text, true);
      break;
    case 'response':
      appendMessage(msg.text, 'agent');
      sendBtn.disabled = false;
      setStatus('Connected', true);
      break;
    case 'taskResult':
      appendTaskPlan(msg.goal, msg.subtasks);
      sendBtn.disabled = false;
      setStatus('Connected', true);
      break;
    case 'error':
      appendMessage(msg.text, 'error');
      sendBtn.disabled = false;
      break;
    case 'health':
      setStatus(msg.ok ? 'Connected (' + msg.url + ')' : 'Disconnected (' + msg.url + ')', msg.ok);
      break;
    case 'prefill':
      inputBox.value = msg.text;
      inputBox.focus();
      break;
  }
});

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// 初始健康检查
vscode.postMessage({ type: 'checkHealth' });

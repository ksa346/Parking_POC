import React, { useState, useRef, useEffect } from 'react';
import * as API from '../services/api';

export default function ChatPanel({ occupancy }) {
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages, loading]);

  const escapeHtml = (str) => {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  };

  async function handleSend() {
    const msg = input.trim();
    if (!msg) return;

    setMessages((prev) => [...prev, { role: 'user', html: escapeHtml(msg) }]);
    setInput('');
    setLoading(true);

    let reply;
    try {
      const res = await API.chat(msg, history);
      reply = res.reply;
    } catch (err) {
      reply = `Sorry, I couldn't reach the assistant. (${err.message || 'unknown error'})`;
    }

    setLoading(false);
    setMessages((prev) => [...prev, { role: 'bot', html: reply }]);
    setHistory((prev) => [...prev, { role: 'user', content: msg }, { role: 'assistant', content: reply }]);
  }

  return (
    <div className="ai-card">
      <div className="ai-header">
        <i className="fas fa-robot"></i>
        <h3>Parking Assistant</h3>
      </div>
      <div className="ai-chat" ref={chatRef}>
        {messages.map((m, i) => (
          <div key={i} className={`ai-message ${m.role}`} dangerouslySetInnerHTML={{ __html: m.html }} />
        ))}
        {loading && (
          <div className="ai-message bot typing">
            <span className="dot-pulse"></span>
          </div>
        )}
      </div>
      <div className="ai-input">
        <input
          type="text"
          placeholder="Ask about parking…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
        />
        <button onClick={handleSend}><i className="fas fa-paper-plane"></i></button>
      </div>
    </div>
  );
}

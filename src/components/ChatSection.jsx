'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Send, Phone, Video, Info, MessageSquareDashed, UserPlus } from 'lucide-react';
import { useRouter } from 'next/navigation';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const ChatSection = () => {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState(null);
  const [connections, setConnections] = useState([]);
  const [activeChat, setActiveChat] = useState(null);
  const [loading, setLoading] = useState(true);
  const [msgInput, setMsgInput] = useState('');
  const [chatHistory, setChatHistory] = useState([]);

  const socketRef = useRef(null);
  const messagesEndRef = useRef(null);

  // Auto scroll to latest message
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [chatHistory]);

  // 1. Fetch Current Logged-In User
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const stored = localStorage.getItem('ucw_current_user');
      if (stored) {
        try {
          setCurrentUser(JSON.parse(stored));
        } catch (e) {
          console.error('Error parsing current user', e);
        }
      }
    }
  }, []);

  // 2. Fetch Connections List
  useEffect(() => {
    fetchAcceptedConnections();
  }, []);

  const fetchAcceptedConnections = async () => {
    setLoading(true);
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('ucw_access_token') : null;
      if (!token) {
        setLoading(false);
        return;
      }

      const res = await fetch(`${API_BASE_URL}/api/connections/list`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const data = await res.json();
        setConnections(data);
        if (data.length > 0) {
          handleSelectChat(data[0]);
        }
      }
    } catch (err) {
      console.error('Failed to load connections:', err);
    } finally {
      setLoading(false);
    }
  };

  // 3. Initialize WebSocket Connection
  useEffect(() => {
    const userId = currentUser?.user_id || currentUser?.id;
    
    if (!userId) {
      console.warn('[WEBSOCKET_DEBUG] Skipping WS connection: No User ID found', currentUser);
      return;
    }

    const wsUrl = `ws://localhost:8000/ws/chat/${userId}`;
    console.log('[WEBSOCKET_DEBUG] Attempting to connect to:', wsUrl);

    const ws = new WebSocket(wsUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      console.log('✅ [WEBSOCKET_SUCCESS] Connection Opened!');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log('📩 [WEBSOCKET_MESSAGE_RECEIVED]', data);

        const currentUserId = String(currentUser?.user_id || currentUser?.id);
        const incomingSenderId = String(data.sender_id);

        // Update chat only if message belongs to current active window user
        setActiveChat((currentActive) => {
          if (currentActive && String(currentActive.other_user_id) === incomingSenderId) {
            setChatHistory((prev) => {
              // Duplicate message rendering fix
              if (data.message_id && prev.some((m) => m.id === data.message_id)) return prev;

              const isMe = incomingSenderId === currentUserId;
              const formattedTime = data.created_at
                ? new Date(data.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

              return [
                ...prev,
                {
                  id: data.message_id || Date.now(),
                  sender: isMe ? 'me' : 'other',
                  text: data.content || data.text,
                  time: formattedTime,
                  sender_id: data.sender_id,
                  receiver_id: data.receiver_id
                }
              ];
            });
          }
          return currentActive;
        });
      } catch (err) {
        console.error('[WEBSOCKET_RECEIVE_ERROR]', err);
      }
    };

    ws.onerror = (err) => {
      console.error('❌ [WEBSOCKET_ERROR] Connection failure:', err);
    };

    ws.onclose = (evt) => {
      console.log(`🔌 [WEBSOCKET_CLOSED] Code: ${evt.code}, Reason: ${evt.reason}`);
    };

    return () => {
      if (ws) ws.close();
    };
  }, [currentUser]);

  // 4. Load Chat History from Database for Selected User
  const handleSelectChat = async (conn) => {
    setActiveChat(conn);
    setChatHistory([
      { id: 'sys-init', sender: 'system', text: `ENCRYPTED SESSION INITIALIZED WITH @${conn.other_username}`, time: 'SYSTEM' }
    ]);

    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('ucw_access_token') : null;
      if (!token) return;

      const res = await fetch(`${API_BASE_URL}/api/chat/history/${conn.other_user_id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const historyData = await res.json();
        const currentUserId = currentUser?.user_id || currentUser?.id;

        const formattedHistory = historyData.map((msg) => ({
          id: msg.message_id,
          sender: String(msg.sender_id) === String(currentUserId) ? 'me' : 'other',
          text: msg.content,
          time: new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          sender_id: msg.sender_id,
          receiver_id: msg.receiver_id
        }));

        setChatHistory([
          { id: 'sys-init', sender: 'system', text: `ENCRYPTED SESSION INITIALIZED WITH @${conn.other_username}`, time: 'SYSTEM' },
          ...formattedHistory
        ]);
      }
    } catch (err) {
      console.error('Failed to load chat history:', err);
    }
  };

  // 5. Send Message over WebSocket
  const handleSend = (e) => {
    e.preventDefault();
    if (!msgInput.trim() || !activeChat || !currentUser) return;

    const currentUserId = currentUser.user_id || currentUser.id;
    const textToSend = msgInput.trim();
    const nowTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const payload = {
      sender_id: currentUserId,
      receiver_id: activeChat.other_user_id,
      content: textToSend,
      created_at: new Date().toISOString()
    };

    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      // 1. Send via WebSocket
      socketRef.current.send(JSON.stringify(payload));

      // 2. Immediate local UI append for Sender
      setChatHistory((prev) => [
        ...prev,
        {
          id: Date.now(),
          sender: 'me',
          text: textToSend,
          time: nowTime,
          sender_id: currentUserId,
          receiver_id: activeChat.other_user_id
        }
      ]);

      setMsgInput('');
    } else {
      console.error('[WEBSOCKET_ERROR] WebSocket is not connected.');
    }
  };

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center font-mono text-white text-xs">
        LOADING_ENCRYPTED_COMMS_CHANNELS...
      </div>
    );
  }

  if (connections.length === 0) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center p-6 text-center">
        <div className="brutalist-panel p-8 max-w-md w-full bg-black border-2 border-white space-y-4">
          <MessageSquareDashed className="w-12 h-12 text-white/40 mx-auto" />
          <h2 className="font-mono text-lg font-bold text-white uppercase">NO_ACTIVE_CHATS</h2>
          <p className="font-mono text-xs text-white/60 uppercase leading-relaxed">
            Search for registered operators, send connection requests, and once accepted your chat channel will initialize here.
          </p>
          <button
            onClick={() => router.push('/')}
            className="brutalist-button py-3 px-6 w-full font-mono text-xs uppercase flex items-center justify-center gap-2"
          >
            <UserPlus className="w-4 h-4" /> Search & Connect Operators
          </button>
        </div>
      </div>
    );
  }

  const formatAvatarUrl = (url) => {
    if (!url || url === 'skipped') return null;
    if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) {
      return url;
    }
    return `${API_BASE_URL}${url.startsWith('/') ? '' : '/'}${url}`;
  };

  return (
    <div className="w-full h-full flex flex-col pt-4 pb-24 px-4 overflow-hidden">

      {/* ─── Active Connections Bar ─────────────────────────────── */}
      <div className="mb-5">
        <h3 className="text-white font-black mb-3 ml-1 uppercase tracking-widest text-xs font-mono border-b border-white/20 pb-2">
          &gt; CONNECTED_CHANNELS ({connections.length})
        </h3>
        <div className="flex gap-3 overflow-x-auto pb-3 custom-scrollbar">
          {connections.map((conn) => {
            const active = activeChat?.connection_id === conn.connection_id;
            const displayName = conn.other_display_name || conn.other_username;
            const initials = conn.other_username.substring(0, 2).toUpperCase();
            const avatarUrl = formatAvatarUrl(conn.other_profile_photo);

            return (
              <button
                key={conn.connection_id}
                onClick={() => handleSelectChat(conn)}
                className={`shrink-0 w-[95px] border-2 p-2 transition-all text-left cursor-pointer
                            ${active
                    ? 'bg-white text-black border-white'
                    : 'bg-black text-white border-white/30 hover:border-white'}`}
              >
                {/* Avatar / Initials block */}
                <div className={`w-full h-10 flex items-center justify-center font-black font-mono text-lg overflow-hidden
                                 ${active ? 'bg-black text-white' : 'bg-white text-black'}`}>
                  {avatarUrl ? (
                    <img src={avatarUrl} alt={displayName} className="w-full h-full object-cover" />
                  ) : (
                    initials
                  )}
                </div>
                <div className="flex justify-between items-center mt-2">
                  <span className="text-[10px] font-mono font-bold uppercase truncate">{displayName}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ─── Main Chat ───────────────────────────────────── */}
      {activeChat && (
        <div className="flex-1 brutalist-panel flex flex-col overflow-hidden min-h-0">

          {/* Chat Header */}
          <div className="px-4 py-3 border-b-2 border-white flex justify-between items-center bg-black shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-white text-black flex items-center justify-center font-black font-mono text-sm overflow-hidden border border-white">
                {formatAvatarUrl(activeChat.other_profile_photo) ? (
                  <img src={formatAvatarUrl(activeChat.other_profile_photo)} alt={activeChat.other_username} className="w-full h-full object-cover" />
                ) : (
                  activeChat.other_username.substring(0, 2).toUpperCase()
                )}
              </div>
              <div>
                <h4 className="text-white font-black tracking-widest text-sm">
                  {activeChat.other_display_name || activeChat.other_username}
                </h4>
                <p className="text-[10px] font-mono text-emerald-400 uppercase">
                  STATUS: CONNECTED (@{activeChat.other_username})
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4 text-white/60">
              <Phone className="w-4 h-4 hover:text-white cursor-pointer transition-colors" />
              <Video className="w-4 h-4 hover:text-white cursor-pointer transition-colors" />
              <Info className="w-4 h-4 hover:text-white cursor-pointer transition-colors" />
            </div>
          </div>

          {/* Messages Container */}
          <div className="flex-1 overflow-y-auto p-4 custom-scrollbar flex flex-col gap-3 bg-black">
            {chatHistory.map((msg) => {
              if (msg.sender === 'system') {
                return (
                  <div key={msg.id} className="text-center my-2">
                    <span className="font-mono text-[10px] text-white/40 border border-white/20 px-3 py-1 uppercase">
                      {msg.text}
                    </span>
                  </div>
                );
              }

              const isMe = msg.sender === 'me';
              return (
                <div key={msg.id} className={`flex ${isMe ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[80%] border-2 p-3
                                   ${isMe
                      ? 'bg-white text-black border-white'
                      : 'bg-black text-white border-white'}`}>
                    <p className="font-mono text-sm leading-relaxed whitespace-pre-wrap">{msg.text}</p>
                    <p className={`text-[10px] mt-2 font-mono font-bold border-t pt-1
                                   ${isMe ? 'border-black/20 text-black/50' : 'border-white/20 text-white/40'}`}>
                      TS:&nbsp;{msg.time}
                    </p>
                  </div>
                </div>
              );
            })}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Bar */}
          <div className="p-3 bg-black border-t-2 border-white shrink-0">
            <form onSubmit={handleSend} className="flex gap-2">
              <input
                type="text"
                value={msgInput}
                onChange={(e) => setMsgInput(e.target.value)}
                placeholder="> INPUT_MESSAGE_"
                className="flex-1 bg-black border-2 border-white px-4 py-3 text-white font-mono
                           text-sm placeholder-white/25 focus:outline-none focus:border-emerald-400
                           uppercase transition-colors"
              />
              <button type="submit" className="brutalist-button px-4 cursor-pointer">
                <Send className="w-4 h-4" />
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatSection;
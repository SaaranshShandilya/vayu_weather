import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiError } from "./api";
import { BrandLogo } from "./BrandLogo";
import { LogoStrip } from "./LogoStrip";
import { ChatMessage } from "./ChatMessage";
import { ArrowIcon, DatabaseIcon, MenuIcon, PlusIcon } from "./icons";
import { LoginScreen } from "./LoginScreen";
import { featuredPrompts } from "./prompts";
import { Sidebar } from "./Sidebar";
import type { ConversationMessage, DataInfo, SessionState } from "./types";

let idCounter = 0;

// crypto.randomUUID exists only in a secure context, so a plain-HTTP
// deployment has to fall back. These ids are React keys, never secrets.
function makeId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  idCounter += 1;
  return `m-${Date.now().toString(36)}-${idCounter}`;
}

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [dataInfo, setDataInfo] = useState<DataInfo | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch(() => setSession({ authenticated: false, configured: false }));
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    api.dataInfo().then(setDataInfo).catch(() => setDataInfo(null));
  }, [session?.authenticated]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function sendMessage(question: string) {
    const cleanQuestion = question.trim();
    if (!cleanQuestion || sending) return;

    const priorMessages = messages;
    const userMessage: ConversationMessage = {
      id: makeId(),
      role: "user",
      content: cleanQuestion,
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setSending(true);

    try {
      const result = await api.chat(cleanQuestion, priorMessages);
      setMessages((current) => [
        ...current,
        {
          id: makeId(),
          role: "assistant",
          content: result.answer,
          result,
        },
      ]);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        setSession((current) => ({
          authenticated: false,
          configured: current?.configured ?? true,
        }));
        return;
      }
      setMessages((current) => [
        ...current,
        {
          id: makeId(),
          role: "assistant",
          content: "",
          error:
            requestError instanceof ApiError
              ? requestError.message
              : "The analysis service could not complete this request.",
          retryQuestion: cleanQuestion,
        },
      ]);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void sendMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(input);
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setMessages([]);
      setSession((current) => ({
        authenticated: false,
        configured: current?.configured ?? true,
      }));
    }
  }

  if (!session) {
    return (
      <main className="app-loading" id="main-content">
        <BrandLogo />
        <p>Opening VayuChat</p>
      </main>
    );
  }

  if (!session.authenticated) {
    return (
      <LoginScreen
        configured={session.configured}
        onAuthenticated={() =>
          setSession({ authenticated: true, configured: session.configured })
        }
      />
    );
  }

  return (
    <div className="app-shell">
      <Sidebar
        open={sidebarOpen}
        dataInfo={dataInfo}
        onClose={() => setSidebarOpen(false)}
        onPrompt={(prompt) => void sendMessage(prompt)}
        onNewChat={() => setMessages([])}
        onLogout={() => void logout()}
      />

      <main className="chat-workspace" id="main-content">
        <LogoStrip />

        <header className="topbar">
          <div className="topbar__left">
            <button
              className="icon-button menu-button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open question library"
            >
              <MenuIcon />
            </button>
            <div>
              <strong>Air quality analyst</strong>
              <span>
                <i />
                Typed functions · Gemini 3.5 Flash-Lite
              </span>
            </div>
          </div>
          <button className="topbar__new" onClick={() => setMessages([])}>
            <PlusIcon />
            <span>New analysis</span>
          </button>
        </header>

        <section className="conversation" aria-label="Conversation">
          {messages.length === 0 ? (
            <div className="empty-state">
              <BrandLogo className="brand-mark--hero" />
              <p className="eyebrow">VayuChat · India air-quality data</p>
              <h1>What do you want to understand?</h1>
              <p className="empty-state__intro">
                Ask about pollution, weather, population, standards, maps, or
                clean-air funding and implementation. VayuChat selects a tested
                analysis function, runs read-only SQL, and shows the source,
                time range, and geographic scope behind the answer.
              </p>
              <div className="featured-prompts">
                {featuredPrompts.map((prompt, index) => (
                  <button key={prompt} onClick={() => void sendMessage(prompt)}>
                    <span>0{index + 1}</span>
                    <strong>{prompt}</strong>
                    <ArrowIcon />
                  </button>
                ))}
              </div>
              <div className="dataset-strip">
                <DatabaseIcon />
                <span>
                  <strong>{dataInfo?.first_date?.slice(0, 4) ?? "2017"}–{dataInfo?.last_date?.slice(0, 4) ?? "2025"}</strong>
                  daily observations
                </span>
                <span>
                  <strong>{dataInfo?.cities ?? "300+"}</strong>
                  cities
                </span>
                <span>
                  <strong>Pollution · weather · Census · funding</strong>
                  linked tabular sources
                </span>
                <span>
                  <strong>Typed SQL functions</strong>
                  validated execution
                </span>
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  onPrompt={(prompt) => void sendMessage(prompt)}
                />
              ))}
              {sending ? (
                <article className="message message--assistant" aria-live="polite">
                  <BrandLogo className="brand-mark--avatar" />
                  <div className="assistant-body">
                    <div className="assistant-meta">
                      <strong>VayuChat</strong>
                      <span>Selecting and running an analysis function</span>
                    </div>
                    <div className="analysis-skeleton">
                      <i />
                      <i />
                      <i />
                    </div>
                  </div>
                </article>
              ) : null}
              <div ref={chatEndRef} />
            </div>
          )}
        </section>

        <div className="composer-wrap">
          <form className="composer" onSubmit={handleSubmit}>
            <label htmlFor="chat-input">Ask about India’s air quality data</label>
            <textarea
              ref={textareaRef}
              id="chat-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="e.g. Compare Delhi’s winter PM2.5 trend from 2020 to 2023"
              rows={1}
              maxLength={2_000}
              disabled={sending}
            />
            <button type="submit" disabled={sending || !input.trim()} aria-label="Send question">
              <ArrowIcon />
            </button>
          </form>
          <p>
            NLQ → Gemini 3.5 Flash-Lite selects a typed function → VayuChat validates
            and runs read-only DuckDB SQL → answer, table, or chart.
          </p>
        </div>
      </main>
    </div>
  );
}

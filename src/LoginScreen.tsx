import { useState, type FormEvent } from "react";
import { api, ApiError } from "./api";
import { BrandLogo } from "./BrandLogo";
import { ArrowIcon, LockIcon } from "./icons";

type LoginScreenProps = {
  configured: boolean;
  onAuthenticated: () => void;
};

export function LoginScreen({ configured, onAuthenticated }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!password || submitting) return;
    setError("");
    setSubmitting(true);
    try {
      await api.login(password);
      setPassword("");
      onAuthenticated();
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Sign-in failed. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell" id="main-content">
      <section className="login-story" aria-labelledby="login-heading">
        <div className="brand-lockup brand-lockup--light">
          <BrandLogo />
          <span>
            <strong>VayuChat</strong>
            <small>Sustainability Lab · IIT Gandhinagar</small>
          </span>
        </div>

        <div className="login-copy">
          <p className="eyebrow">Air quality, made queryable</p>
          <h1 id="login-heading">Ask India’s air what changed.</h1>
          <p>
            Explore daily pollution, weather, city, state, and NCAP funding data
            from 2017–2025 with a Gemini-powered analyst.
          </p>
        </div>

        <div className="login-metrics" aria-label="Dataset summary">
          <span>
            <strong>2017–25</strong>
            daily observations
          </span>
          <span>
            <strong>277</strong>
            Indian cities
          </span>
          <span>
            <strong>24</strong>
            measured fields
          </span>
        </div>
      </section>

      <section className="login-panel" aria-label="Sign in">
        <div className="login-card">
          <div className="institution-mark" aria-label="IIT Gandhinagar">
            <img
              src="/iitgn-logo.png"
              alt="IIT Gandhinagar emblem"
              width="512"
              height="512"
            />
            <div>
              <strong>Indian Institute of Technology</strong>
              <small>Gandhinagar</small>
            </div>
          </div>
          <div className="login-card__heading">
            <span className="lock-tile">
              <LockIcon />
            </span>
            <div>
              <p className="eyebrow">Protected workspace</p>
              <h2>Enter the shared password</h2>
            </div>
          </div>

          {configured ? (
            <form onSubmit={handleSubmit}>
              <label htmlFor="password">Password</label>
              <div className="password-row">
                <input
                  id="password"
                  name="password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  autoFocus
                  required
                />
                <button type="submit" disabled={submitting || !password}>
                  <span>{submitting ? "Checking" : "Continue"}</span>
                  <ArrowIcon />
                </button>
              </div>
              {error ? (
                <p className="form-error" role="alert">
                  {error}
                </p>
              ) : (
                <p className="form-help">
                  Access lasts for 12 hours on this browser.
                </p>
              )}
            </form>
          ) : (
            <div className="configuration-note" role="status">
              <strong>This Space still needs its secrets.</strong>
              <span>
                Add <code>APP_PASSWORD</code> and <code>GEMINI_API_KEY</code> in
                the Hugging Face Space settings.
              </span>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

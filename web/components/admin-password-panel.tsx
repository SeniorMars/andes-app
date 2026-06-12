"use client";

import { FormEvent, useState } from "react";

interface AdminPasswordPanelProps {
  error?: string;
  onSubmit: (password: string) => void;
}

export function AdminPasswordPanel({ error, onSubmit }: AdminPasswordPanelProps) {
  const [password, setPassword] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(password);
  }

  return (
    <section className="panel pad">
      <form className="form" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="admin-password">Admin password</label>
          <input
            autoComplete="current-password"
            autoFocus
            id="admin-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {error ? <p className="error">{error}</p> : null}
        <div className="actions">
          <span className="subtle">Stored for this browser session.</span>
          <button className="button primary" disabled={!password.trim()} type="submit">
            Unlock admin
          </button>
        </div>
      </form>
    </section>
  );
}

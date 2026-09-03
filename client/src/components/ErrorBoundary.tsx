import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Remounts the boundary when it changes, clearing a previous crash. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

/**
 * Keeps one broken pane from taking the app down.
 *
 * React unmounts the entire tree when a render or effect throws, so before
 * this existed a crash inside the editor left an empty white page with the
 * reason visible only in the browser console — the worst thing that can
 * happen in the middle of a demo. Here the rest of the app keeps working and
 * the error is on screen where it can be read.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash">
        <strong>This panel failed to render.</strong>
        <pre>{this.state.error.message}</pre>
        <button onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}

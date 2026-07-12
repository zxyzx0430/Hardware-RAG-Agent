import { Component, type ReactNode } from "react";
import { useLogStore } from "../../stores/useLogStore";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    console.error("ErrorBoundary caught:", error, info);
    try {
      useLogStore.getState().log("error", "ui", `ErrorBoundary: ${error.message}`);
    } catch { /* logger may not be available */ }
  }

  handleReset = () => {
    // 清除可能损坏的 localStorage 数据
    const keys = Object.keys(localStorage).filter((k) => k.startsWith("hwrag_"));
    keys.forEach((k) => localStorage.removeItem(k));
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          padding: 40,
          fontFamily: "system-ui, sans-serif",
          background: "#0d1117",
          color: "#e6edf3",
        }}>
          <h2 style={{ fontSize: 20, marginBottom: 12 }}>应用出错</h2>
          <pre style={{
            background: "#161b22",
            border: "1px solid #30363d",
            borderRadius: 8,
            padding: 16,
            maxWidth: 600,
            overflow: "auto",
            fontSize: 13,
            color: "#f85149",
            whiteSpace: "pre-wrap",
          }}>
            {this.state.error?.message}
            {"\n"}
            {this.state.error?.stack?.split("\n").slice(0, 5).join("\n")}
          </pre>
          <button
            onClick={this.handleReset}
            style={{
              marginTop: 20,
              padding: "8px 24px",
              borderRadius: 6,
              border: "1px solid #30363d",
              background: "#238636",
              color: "#fff",
              cursor: "pointer",
              fontSize: 14,
            }}
          >
            清除缓存并重置
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

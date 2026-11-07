import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ensureAuthSession } from "./api/client";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import "./styles/globals.css";

const queryClient = new QueryClient();

const SESSION_BOOTSTRAP_TIMEOUT_MS = 2500;

async function bootstrap() {
  // Recover the local session from the saved API key before the explorer and
  // chat mount, so a stale or missing session token never surfaces as "login".
  await Promise.race([
    ensureAuthSession(),
    new Promise<void>((resolve) => setTimeout(resolve, SESSION_BOOTSTRAP_TIMEOUT_MS)),
  ]);
  createRoot(document.getElementById("root")!).render(
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

void bootstrap();

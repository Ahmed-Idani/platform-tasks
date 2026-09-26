import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./index.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster
        position="bottom-right"
        theme="system"
        toastOptions={{
          className: "!font-sans !rounded-lg !border-border !bg-surface !text-fg",
          descriptionClassName: "!text-muted",
        }}
      />
    </QueryClientProvider>
  </StrictMode>,
);

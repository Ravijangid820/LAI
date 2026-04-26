import { Logo } from "@/react-app/components/Logo";

export function TypingIndicator({ message = "LAI is thinking..." }: { message?: string }) {
  return (
    <div className="flex gap-4 py-6">
      {/* Logo replaces Bot (lucide) */}
      <div className="w-9 h-9 flex items-center justify-center flex-shrink-0">
        <Logo size="sm" showText={false} />
      </div>
      <div className="flex items-center gap-2 px-4 py-3 rounded-2xl rounded-tl-sm bg-muted/50">
        <div className="flex gap-1">
          <span
            className="w-2 h-2 bg-primary/60 rounded-full animate-bounce"
            style={{ animationDelay: "0ms" }}
          />
          <span
            className="w-2 h-2 bg-primary/60 rounded-full animate-bounce"
            style={{ animationDelay: "150ms" }}
          />
          <span
            className="w-2 h-2 bg-primary/60 rounded-full animate-bounce"
            style={{ animationDelay: "300ms" }}
          />
        </div>
        <span className="text-sm text-muted-foreground ml-2">
          {message}
        </span>
      </div>
    </div>
  );
}

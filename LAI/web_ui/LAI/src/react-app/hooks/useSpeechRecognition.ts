import { useState, useEffect, useRef, useCallback } from "react";

// ── Web Speech API type declarations ─────────────────────────────────────────
// These APIs exist in all modern browsers but are absent from TypeScript's
// default lib. We declare them locally so the build doesn't rely on
// a specific TS lib version.

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionResult {
  readonly length: number;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
  isFinal: boolean;
}

interface SpeechRecognitionResultList {
  readonly length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message: string;
}

interface ISpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror:  ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend:    (() => void) | null;
  start():  void;
  stop():   void;
  abort():  void;
}

interface ISpeechRecognitionConstructor {
  new (): ISpeechRecognition;
}

declare global {
  interface Window {
    SpeechRecognition:       ISpeechRecognitionConstructor | undefined;
    webkitSpeechRecognition: ISpeechRecognitionConstructor | undefined;
  }
}

// ─────────────────────────────────────────────────────────────────────────────

export type MicState = "idle" | "listening" | "error" | "unsupported";

interface UseSpeechRecognitionOptions {
  onTranscript: (text: string) => void;
  onEnd?: () => void;
  language?: string;
}

interface UseSpeechRecognitionReturn {
  micState: MicState;
  errorMessage: string | null;
  isSupported: boolean;
  toggleListening: (currentText?: string) => void;
}

export function useSpeechRecognition({
  onTranscript,
  onEnd,
  language = "en-US",
}: UseSpeechRecognitionOptions): UseSpeechRecognitionReturn {
  const [micState, setMicState]         = useState<MicState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const recognitionRef   = useRef<ISpeechRecognition | null>(null);
  const finalizedRef     = useRef("");
  const baseTextRef      = useRef("");
  const shouldRestartRef = useRef(false);
  const silenceTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);

  const SILENCE_TIMEOUT_MS = 8000;

  const isSupported =
    typeof window !== "undefined" &&
    (window.SpeechRecognition != null || window.webkitSpeechRecognition != null);

  const clearSilenceTimer = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  }, []);

  const resetSilenceTimer = useCallback(
    (stopFn: () => void) => {
      clearSilenceTimer();
      silenceTimerRef.current = setTimeout(() => {
        stopFn();
      }, SILENCE_TIMEOUT_MS);
    },
    [clearSilenceTimer],
  );

  useEffect(() => {
    return () => {
      clearSilenceTimer();
      shouldRestartRef.current = false;
      recognitionRef.current?.abort();
    };
  }, [clearSilenceTimer]);

  const buildAndStart = useCallback((): ISpeechRecognition | undefined => {
    if (!isSupported) return undefined;

    const SR = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!SR) return undefined;

    const recognition = new SR();

    recognition.lang            = language;
    recognition.interimResults  = true;
    recognition.maxAlternatives = 1;
    recognition.continuous      = false;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      resetSilenceTimer(() => {
        shouldRestartRef.current = false;
        recognitionRef.current?.stop();
        setMicState("idle");
        onEnd?.();
      });

      let interim = "";
      let final   = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) {
          final += r[0].transcript;
        } else {
          interim += r[0].transcript;
        }
      }

      if (final) {
        finalizedRef.current +=
          (finalizedRef.current ? " " : "") + final.trim();
        interim = "";
      }

      const parts = [baseTextRef.current, finalizedRef.current, interim]
        .map((s) => s.trim())
        .filter(Boolean);

      onTranscript(parts.join(" "));
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      if (event.error === "aborted") return;
      if (event.error === "no-speech") return;

      const messages: Record<string, string> = {
        "not-allowed":         "Microphone access denied. Please allow microphone permission.",
        "network":             "Network error. Check your connection.",
        "audio-capture":       "No microphone found.",
        "service-not-allowed": "Speech service not allowed.",
      };
      setErrorMessage(messages[event.error] ?? `Error: ${event.error}`);
      setMicState("error");
      shouldRestartRef.current = false;
      clearSilenceTimer();
    };

    recognition.onend = () => {
      if (shouldRestartRef.current) {
        try {
          const next = buildAndStart();
          if (next) recognitionRef.current = next;
        } catch (_) {
          setMicState("idle");
          shouldRestartRef.current = false;
          clearSilenceTimer();
          onEnd?.();
        }
      } else {
        setMicState("idle");
        clearSilenceTimer();
        onEnd?.();
      }
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch (_) {
      // Already started — ignore
    }

    return recognition;
  }, [isSupported, language, onTranscript, onEnd, resetSilenceTimer, clearSilenceTimer]);

  const startListening = useCallback(
    (existingText: string) => {
      if (!isSupported) {
        setMicState("unsupported");
        return;
      }

      finalizedRef.current     = "";
      baseTextRef.current      = existingText.trim();
      shouldRestartRef.current = true;

      setErrorMessage(null);
      setMicState("listening");

      const stopAll = () => {
        shouldRestartRef.current = false;
        recognitionRef.current?.stop();
        setMicState("idle");
        onEnd?.();
      };
      resetSilenceTimer(stopAll);

      buildAndStart();
    },
    [isSupported, buildAndStart, resetSilenceTimer, onEnd],
  );

  const stopListening = useCallback(() => {
    shouldRestartRef.current = false;
    clearSilenceTimer();
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setMicState("idle");
    onEnd?.();
  }, [clearSilenceTimer, onEnd]);

  const toggleListening = useCallback(
    (currentText = "") => {
      if (micState === "listening") {
        stopListening();
      } else {
        startListening(currentText);
      }
    },
    [micState, startListening, stopListening],
  );

  return { micState, errorMessage, isSupported, toggleListening };
}
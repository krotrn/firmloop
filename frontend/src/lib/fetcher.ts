import { errorCodeValues, type components } from "@/types/generated/api";

export type ErrorCode = components["schemas"]["ErrorCode"];

export class ApiError extends Error {
  status: number;
  code: ErrorCode;
  details: Record<string, unknown>;

  constructor(
    status: number,
    code: ErrorCode,
    message: string,
    details: Record<string, unknown> = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function isApiError(e: unknown, code?: ErrorCode): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isErrorCode(value: unknown): value is ErrorCode {
  return (
    typeof value === "string" &&
    (errorCodeValues as ReadonlyArray<string>).includes(value)
  );
}

function parseErrorBody(body: unknown): {
  code: ErrorCode;
  message: string;
  details: Record<string, unknown>;
} | null {
  if (!isRecord(body) || !isRecord(body.error)) return null;
  const { code, message, details } = body.error;
  if (!isErrorCode(code) || typeof message !== "string" || !isRecord(details)) {
    return null;
  }
  return { code, message, details };
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const parsed = parseErrorBody(body);
  if (parsed) {
    return new ApiError(
      response.status,
      parsed.code,
      parsed.message,
      parsed.details
    );
  }
  return new ApiError(
    response.status,
    "INTERNAL",
    response.statusText || `Request failed with status ${response.status}`
  );
}

export async function fetcher<T>(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<T> {
  // Only declare a JSON body when there is one: a Content-Type on a
  // cross-origin GET would force a needless CORS preflight.
  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(input, { ...init, headers });

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      response.status,
      "INTERNAL",
      "The server sent a response that is not valid JSON"
    );
  }
}

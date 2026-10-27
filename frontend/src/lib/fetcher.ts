export class FetchError extends Error {
  status: number;
  info?: unknown;

  constructor(message: string, status: number, info?: unknown) {
    super(message);
    this.name = "FetchError";
    this.status = status;
    this.info = info;
  }
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
    let errorInfo: unknown;
    try {
      errorInfo = await response.json();
    } catch {
      errorInfo = await response.text();
    }

    throw new FetchError(
      `An error occurred while fetching the data: ${response.statusText}`,
      response.status,
      errorInfo
    );
  }

  return response.json() as Promise<T>;
}

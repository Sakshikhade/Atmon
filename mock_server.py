import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read the content length to fetch the request body
        content_length = int(self.headers["Content-Length"])
        post_data = self.rfile.read(content_length)

        try:
            # Parse raw bytes into JSON
            payload = json.loads(post_data.decode("utf-8"))

            # Print beautiful premium formatted console alert
            print("\n" + "🔥" * 25)
            print("🔔 NEW WEBHOOK ALERT DETECTED!")
            print(f"Timestamp:   {payload.get('timestamp')}")
            print(f"Status:      {str(payload.get('status')).upper()}")
            print(f"Event ID:    {payload.get('event_id')}")
            print(f"Event Type:  {payload.get('event_type')}")
            if payload.get("detail"):
                print(f"Detail:      {payload.get('detail')}")
            print("-" * 50)
            metadata = payload.get("metadata", {})
            print(f"Frequency:   {metadata.get('frequency_hz'):.2f} Hz")
            print(f"Average Vel: {metadata.get('avg_velocity'):.4f}")
            print(f"Duration:    {metadata.get('duration_seconds'):.1f} seconds")
            print("🔥" * 25 + "\n")

            # Respond with HTTP 200 OK
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"status": "success", "message": "Alert processed locally"}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Bad Request: {e}".encode())

    def log_message(self, format, *args):
        # Override standard log handler to prevent polluting terminal with raw POST logs
        pass


def run(port=5001):
    server_address = ("", port)
    httpd = HTTPServer(server_address, WebhookHandler)
    print("\n=======================================================")
    print(f"AAMAS Local Alert Gateway Receiver Active on port {port}")
    print("Listening for local webhook dispatches...")
    print("Press Ctrl+C to exit.")
    print("=======================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local webhook listener...")
        httpd.server_close()
        print("Receiver shutdown successfully.")


if __name__ == "__main__":
    run()

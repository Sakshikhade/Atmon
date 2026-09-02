import cv2


def main():
    # Open the default camera
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    print("Press 'q' to quit.")

    while True:
        # Read a frame from the camera
        ret, frame = cap.read()

        if not ret:
            print("Error: Failed to grab frame.")
            break

        # Overlay text on the frame
        text = "System Active"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 1
        font_color = (0, 255, 0)  # Green color in BGR
        thickness = 2
        position = (50, 50)  # (x, y) coordinates

        cv2.putText(frame, text, position, font, font_scale, font_color, thickness, cv2.LINE_AA)

        # Display the frame
        cv2.imshow("Webcam Test", frame)

        # Wait for 1 ms and check if the 'q' key is pressed
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    # Release the camera and close all windows
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

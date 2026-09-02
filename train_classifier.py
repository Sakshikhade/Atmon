import csv
import os
import pickle

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split


def load_or_generate_data(filepath="data/behavior_log.csv"):
    """
    Loads training data from CSV if present. If missing or insufficient,
    generates synthetic data representing stimming, normal, and avoidance behaviors.
    """
    X = []
    y = []

    if os.path.exists(filepath):
        try:
            with open(filepath) as file:
                reader = csv.reader(file)
                next(reader)  # skip header row
                for row in reader:
                    # columns: timestamp, frame_index, mean_distance, std_distance, mean_velocity, peak_frequency, [mean_ear_coverage, mean_eye_openness], label
                    if len(row) < 9:
                        # Fallback for old 7-column schema
                        mean_dist = float(row[2])
                        std_dist = float(row[3])
                        mean_vel = float(row[4])
                        peak_freq = float(row[5])
                        mean_ear = 1.0
                        mean_eye = 1.0
                        label = int(row[6])
                    else:
                        mean_dist = float(row[2])
                        std_dist = float(row[3])
                        mean_vel = float(row[4])
                        peak_freq = float(row[5])
                        mean_ear = float(row[6])
                        mean_eye = float(row[7])
                        label = int(row[8])

                    X.append([mean_dist, std_dist, mean_vel, peak_freq, mean_ear, mean_eye])
                    y.append(label)
            if len(y) >= 20:
                print(f"[Train] Loaded {len(y)} samples from {filepath}")
                return np.array(X), np.array(y)
        except Exception as e:
            print(
                f"[Train Warning] Error reading dataset: {e}. Generating synthetic fallback dataset."
            )

    # Generate Synthetic Data if CSV is missing or too small
    print(
        "[Train Info] No sufficient recorded data found. Generating synthetic training profiles..."
    )
    np.random.seed(42)

    # Class 0: Normal Behaviors (Static posture, slow movements, low variance, non-rhythmic, open eyes/ears)
    for _ in range(500):
        mean_dist = np.random.uniform(0.4, 0.8)
        std_dist = np.random.uniform(0.005, 0.04)
        mean_vel = np.random.uniform(0.001, 0.025)
        peak_freq = np.random.uniform(0.0, 2.0)
        mean_ear = np.random.uniform(0.6, 1.5)
        mean_eye = np.random.uniform(0.7, 1.0)
        X.append([mean_dist, std_dist, mean_vel, peak_freq, mean_ear, mean_eye])
        y.append(0)

    # Class 1: Stimming Behaviors (Rapid rhythmic motion, high velocity, high variance, open eyes/ears)
    for _ in range(500):
        mean_dist = np.random.uniform(0.5, 1.2)
        std_dist = np.random.uniform(0.12, 0.45)
        mean_vel = np.random.uniform(0.045, 0.18)
        peak_freq = np.random.uniform(3.0, 6.5)
        mean_ear = np.random.uniform(0.6, 1.5)
        mean_eye = np.random.uniform(0.7, 1.0)
        X.append([mean_dist, std_dist, mean_vel, peak_freq, mean_ear, mean_eye])
        y.append(1)

    # Class 2: Avoidance Behaviors (Low ear coverage or low eye openness)
    for _ in range(250):
        # Sub-class A: Covering ears
        mean_dist = np.random.uniform(0.2, 0.5)
        std_dist = np.random.uniform(0.01, 0.06)
        mean_vel = np.random.uniform(0.005, 0.03)
        peak_freq = np.random.uniform(0.0, 2.5)
        mean_ear = np.random.uniform(0.01, 0.12)  # Under the 0.15 threshold
        mean_eye = np.random.uniform(0.7, 1.0)
        X.append([mean_dist, std_dist, mean_vel, peak_freq, mean_ear, mean_eye])
        y.append(2)

    for _ in range(250):
        # Sub-class B: Closing eyes
        mean_dist = np.random.uniform(0.3, 0.7)
        std_dist = np.random.uniform(0.01, 0.06)
        mean_vel = np.random.uniform(0.005, 0.03)
        peak_freq = np.random.uniform(0.0, 2.5)
        mean_ear = np.random.uniform(0.6, 1.5)
        mean_eye = np.random.uniform(0.01, 0.15)  # Under the 0.20 threshold
        X.append([mean_dist, std_dist, mean_vel, peak_freq, mean_ear, mean_eye])
        y.append(2)

    return np.array(X), np.array(y)


def main():
    print("=======================================================")
    print("AAMAS Random Forest Stimming & Avoidance Classifier Trainer")
    print("=======================================================\n")

    X, y = load_or_generate_data()

    # Split into train & test sets (80/20)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Initialize and train Random Forest Classifier
    rf_clf = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    rf_clf.fit(X_train, y_train)

    # Perform validation
    y_pred = rf_clf.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)

    print("\n--- Model Evaluation ---")
    print(f"Test Set Accuracy: {accuracy * 100:.2f}%")
    print("\nClassification Report:")

    unique_labels = np.unique(y_test)
    target_names = []
    if 0 in unique_labels:
        target_names.append("Normal (0)")
    if 1 in unique_labels:
        target_names.append("Stimming (1)")
    if 2 in unique_labels:
        target_names.append("Avoidance (2)")

    print(classification_report(y_test, y_pred, target_names=target_names))

    # Save the model
    model_dir = "models"
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "stimming_classifier.pkl")

    try:
        with open(model_path, "wb") as f:
            pickle.dump(rf_clf, f)
        print(f"\n[Success] Model serialized and saved to: {model_path}")
    except Exception as e:
        print(f"\n[Error] Failed to serialize model: {e}")


if __name__ == "__main__":
    main()

import load_data

def process_json(data):
    weather = data["weather"]

    temperature = weather["temperature"]
    humidity = weather["humidity"]
    rainfall = weather["rainfall"]
    windspeed = weather["windspeed"]

    scores_predictions = {}

    for load in data["loads"]:
        load_id = load["load_id"]
        voltage = load["voltage"]
        current = load["current"]
        state = load["state"]

        if state == 0:
            continue

        ratings = load_data.load_metadata[load_id]

        power = voltage * current
        voltage_deviation = voltage - ratings["rated_voltage"]
        current_deviation = current - ratings["rated_current"]

        feature_vector = [
            power,
            voltage_deviation,
            current_deviation,
            temperature,
            humidity,
            windspeed,
            rainfall
        ]

        score, prediction = anomaly_detection(load_id, feature_vector)

        scores_predictions[load_id] = {
            "score": score,
            "prediction": prediction
        }

    system_anomaly_score = system_score_calc(scores_predictions)

    return system_anomaly_score, scores_predictions

def anomaly_detection(load_id, feature_vector):
    model = load_data.models[load_id]

    score = model.decision_function([feature_vector])[0]

    prediction = model.predict([feature_vector])[0]

    return score, prediction

def system_score_calc(scores_predictions):
    scores = [
        values["score"]
        for values in scores_predictions.values()
    ]

    system_anomaly_score = (1 / len(scores)) * sum(scores)

    return system_anomaly_score
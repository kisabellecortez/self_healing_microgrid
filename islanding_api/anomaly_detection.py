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

        # Skip an unrecognized load_id (e.g. hardware reports an id that
        # hasn't been seeded into load_metadata yet) instead of crashing the
        # whole request with a KeyError over one bad/unexpected reading.
        ratings = load_data.load_metadata.get(load_id)
        if ratings is None:
            continue

        # Skip a load with metadata but no trained model yet - real before
        # the first retrain has ever run (see NEXT_STEPS.md), and possible
        # any time a new load is added to node_data before its first model
        # exists. Previously this KeyError'd inside anomaly_detection()
        # below and 500'd the entire request over one not-yet-trained load.
        if load_id not in load_data.models:
            continue

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
            "prediction": prediction,
            # Added for historic_grid_data logging (main.py) - power/
            # voltage_deviation/current_deviation were already computed
            # above for the model's feature_vector, just not returned
            # before. Purely additive: score/prediction are unchanged.
            "power": power,
            "voltage_deviation": voltage_deviation,
            "current_deviation": current_deviation,
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

    # All loads can be disconnected (every load's state == 0 in the JSON
    # payload), in which case scores_predictions is empty and there is no
    # electrical behavior to score. None signals "no signal" to the caller
    # rather than raising ZeroDivisionError or claiming a fake score of 0.0.
    if not scores:
        return None

    system_anomaly_score = (1 / len(scores)) * sum(scores)

    return system_anomaly_score
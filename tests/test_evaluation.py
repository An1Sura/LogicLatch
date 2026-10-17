from evaluate import evaluate


def test_synthetic_acceptance_gates():
    report = evaluate()
    assert report['episodes'] == 70
    assert report['recall'] == 1
    assert report['precision'] == 1
    assert report['healthy_false_alarm_rate'] == 0
    assert report['max_detection_latency_seconds'] <= 1.1

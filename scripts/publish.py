import json
import os
import sys
import uuid

import pika

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://app:app@localhost:5672/%2F")
QUEUE = "tasks.llm_inference"

count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "testdata", "sample.txt")
max_words = int(sys.argv[3]) if len(sys.argv) > 3 else 150

with open(path) as f:
  text = f.read()  # read here, sent inside the message, like the API will do

connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
channel = connection.channel()
channel.queue_declare(queue=QUEUE, durable=True)
channel.confirm_delivery()  # broker confirms each message is stored, or raises

for _ in range(count):
  task = {"task_id": str(uuid.uuid4()), "text": text, "max_words": max_words}
  channel.basic_publish(
    exchange="",
    routing_key=QUEUE,
    body=json.dumps(task),
    properties=pika.BasicProperties(
      delivery_mode=pika.DeliveryMode.Persistent,  # message survives a RabbitMQ restart
      content_type="application/json",
    ),
  )
  print("published", task["task_id"])

connection.close()

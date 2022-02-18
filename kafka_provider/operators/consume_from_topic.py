from functools import partial
from typing import Any, Callable, Dict, Optional, Sequence, Union

from airflow.exceptions import AirflowException
from airflow.models import BaseOperator

from kafka_provider.hooks.consumer import KafkaConsumerHook
from kafka_provider.shared_utils import get_callable

VALID_COMMIT_CADENCE = {"never", "end_of_batch", "end_of_operator"}


class ConsumeFromTopicOperator(BaseOperator):

    BLUE = "#ffefeb"
    ui_color = BLUE

    def __init__(
        self,
        topics: Sequence[str],
        apply_function: Union[Callable[..., Any], str],
        apply_function_args: Optional[Sequence[Any]] = None,
        apply_function_kwargs: Optional[Dict[Any, Any]] = None,
        kafka_conn_id: Optional[str] = None,
        consumer_config: Optional[Dict[Any, Any]] = None,
        commit_cadence: Optional[str] = "end_of_operator",
        max_messages: Optional[int] = None,
        max_batch_size: int = 1000,
        poll_timeout: Optional[float] = 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.topics = topics
        self.apply_function = apply_function
        self.apply_function_args = apply_function_args or ()
        self.apply_function_kwargs = apply_function_kwargs or {}
        self.kafka_conn_id = kafka_conn_id
        self.config = consumer_config or {}
        self.commit_cadence = commit_cadence
        self.max_messages = max_messages or True
        self.max_batch_size = max_batch_size
        self.poll_timeout = poll_timeout

        if self.commit_cadence not in VALID_COMMIT_CADENCE:
            raise AirflowException(
                f"commit_cadence must be one of {VALID_COMMIT_CADENCE}. Got {self.commit_cadence}"
            )

        if self.max_messages and self.max_batch_size > self.max_messages:
            self.log.warn(
                f"max_batch_size ({self.max_batch_size}) > max_messages"
                f" ({self.max_messages}). Setting max_messages to"
                f" {self.max_batch_size}"
            )

        if self.commit_cadence == "never":
            self.commit_cadence = None

    def execute(self, context) -> Any:

        consumer = KafkaConsumerHook(
            topics=self.topics, kafka_conn_id=self.kafka_conn_id, config=self.config
        ).get_consumer()

        if isinstance(self.apply_function, str):
            self.apply_function = get_callable(self.apply_function)

        apply_callable = self.apply_function
        apply_callable = partial(apply_callable, *self.apply_function_args, **self.apply_function_kwargs)

        messages_left = self.max_messages
        messages_processed = 0

        while messages_left > 0:  # bool(True > 0) == True

            if not isinstance(messages_left, bool):
                batch_size = self.max_batch_size if messages_left > self.max_batch_size else messages_left
            else:
                batch_size = self.max_batch_size

            msgs = consumer.consume(num_messages=batch_size, timeout=self.poll_timeout)
            messages_left -= len(msgs)
            messages_processed += len(msgs)

            if not msgs:  # No messages + messages_left is being used.
                self.log.info("Reached end of log. Exiting.")
                break

            for m in msgs:
                apply_callable(m)

            if self.commit_cadence == "end_of_batch":
                self.log.info(f"committing offset at {self.commit_cadence}")
                consumer.commit()

        if self.commit_cadence:
            self.log.info(f"committing offset at {self.commit_cadence}")
            consumer.commit()

        consumer.close()

        return

import argparse
import os

from openai import OpenAI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal OpenAI GPT-5.5 request demo")
    parser.add_argument("--prompt", default="你好，请用一句话介绍你自己。", help="User prompt")
    parser.add_argument("--system", default="你是一个简洁的助手。", help="System prompt")
    parser.add_argument("--model", default="gpt-5.5", help="Model name")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling parameter")
    parser.add_argument(
        "--api-base",
        default=os.getenv("OPENAI_BASE_URL", "https://uniapi.ruijie.com.cn/v1"),
        help="API base URL",
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "sk-BSsg7YfI4HftGEX7NHrRDroRfuSLqeTjVVl8mTRQ21yBsKA5"), help="API key")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=256,
        help="Maximum output token count",
    )
    return parser.parse_args()


def main() -> None:
	args = parse_args()

	if not args.api_key:
		raise RuntimeError("OPENAI_API_KEY is not set and --api-key is empty")

	client = OpenAI(
		base_url=args.api_base,
		api_key=args.api_key,
	)

	response = client.chat.completions.create(
		model=args.model,
		messages=[
			{"role": "system", "content": args.system},
			{"role": "user", "content": args.prompt},
		],
		temperature=args.temperature,
		top_p=args.top_p,
		# max_tokens=args.max_output_tokens,
	)

	print(response.choices[0].message.content)


if __name__ == "__main__":
    main()

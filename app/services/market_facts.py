from typing import Any


def format_market_fact_value(
    value: Any,
) -> str:
    if value is None:
        return "null"

    if isinstance(value, bool):
        return str(value).lower()

    cleaned_value = (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("`", "'")
        .strip()
    )

    return cleaned_value


def append_fact(
    lines: list[str],
    *,
    label: str,
    value: Any,
    indent: str = "",
) -> None:
    lines.append(
        f"{indent}- {label}: "
        f"{format_market_fact_value(value)}"
    )


def build_market_lens_facts(
    metadata: dict[str, Any],
) -> str | None:
    authoritative_summaries = metadata.get(
        "authoritative_summaries"
    )

    if not isinstance(
        authoritative_summaries,
        list,
    ):
        return None

    lines = [
        "**Retrieved Facts (deterministic):**",
        (
            "These values were generated directly "
            "from retrieved Market Lens records and "
            "were not rewritten by the language "
            "model."
        ),
        "",
    ]

    append_fact(
        lines,
        label="retrieved_asset_records",
        value=metadata.get(
            "asset_record_count",
            0,
        ),
    )

    append_fact(
        lines,
        label="retrieved_price_records",
        value=metadata.get(
            "price_record_count",
            0,
        ),
    )

    append_fact(
        lines,
        label="retrieved_analytics_records",
        value=metadata.get(
            "analytics_record_count",
            0,
        ),
    )

    append_fact(
        lines,
        label="retrieved_signal_records",
        value=metadata.get(
            "signal_record_count",
            0,
        ),
    )

    for index, summary in enumerate(
        authoritative_summaries,
        start=1,
    ):
        if not isinstance(summary, dict):
            continue

        lines.extend(
            [
                "",
                f"**Asset {index}:**",
            ]
        )

        asset = summary.get("asset")

        if isinstance(asset, dict):
            for field_name in (
                "id",
                "symbol",
                "name",
                "asset_type",
                "exchange",
                "currency",
                "sector",
                "industry",
                "is_active",
                "created_at",
                "updated_at",
            ):
                if field_name not in asset:
                    continue

                append_fact(
                    lines,
                    label=field_name,
                    value=asset[field_name],
                )

        price_summary = summary.get(
            "price_summary"
        )

        if isinstance(
            price_summary,
            dict,
        ):
            lines.append(
                "- price_summary:"
            )

            for field_name in (
                "record_count",
                "earliest_record_date",
                "latest_record_date",
                "latest_close_price",
                "lowest_close_price",
                "highest_close_price",
            ):
                if (
                    field_name
                    not in price_summary
                ):
                    continue

                append_fact(
                    lines,
                    label=field_name,
                    value=(
                        price_summary[
                            field_name
                        ]
                    ),
                    indent="  ",
                )

        analytics = summary.get(
            "calculated_analytics"
        )

        if isinstance(analytics, dict):
            lines.append(
                "- calculated_analytics:"
            )

            for (
                field_name,
                value,
            ) in analytics.items():
                append_fact(
                    lines,
                    label=field_name,
                    value=value,
                    indent="  ",
                )

        if (
            "signal_record_count"
            in summary
        ):
            append_fact(
                lines,
                label=(
                    "signal_record_count"
                ),
                value=summary[
                    "signal_record_count"
                ],
            )

    return "\n".join(lines)
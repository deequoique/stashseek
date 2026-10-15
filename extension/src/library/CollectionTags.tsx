interface CollectionTagsProps {
  names: readonly string[];
  className?: string;
}

export function CollectionTags({ names, className = "" }: CollectionTagsProps) {
  if (names.length === 0) return null;
  return (
    <ul className={`collection-tag-list ${className}`.trim()} aria-label="所属收藏夹">
      {names.map((name) => (
        <li className="collection-tag" key={name.toLocaleLowerCase("en")}>#{name}</li>
      ))}
    </ul>
  );
}

export async function up(queryRunner: any): Promise<void> {
  // PLANTED DEFECT: schema. NOT NULL with no default against a populated table,
  // varchar over text, and a cascading delete.
  await queryRunner.query(`ALTER TABLE quotes ADD COLUMN status varchar(32) NOT NULL`);
  await queryRunner.query(
    `ALTER TABLE quotes ADD CONSTRAINT fk_supplier FOREIGN KEY (supplier_id)
     REFERENCES suppliers(id) ON DELETE CASCADE`
  );
}
